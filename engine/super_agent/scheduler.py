"""
APScheduler-based cron scheduler for Super Agents.

Reads agent_schedules from PostgreSQL, executes tasks on schedule,
and manages the lifecycle of scheduled jobs.

Run as a standalone worker:
  python engine/super_agent/scheduler.py

Or import and start in-process (single-worker only).
"""

import os
import sys
import time
import signal
import logging
import traceback
import psycopg2
from psycopg2.extras import RealDictCursor
from datetime import datetime, timezone

# Add engine directory to path
ENGINE_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
BACKEND_DIR = os.path.abspath(os.path.join(ENGINE_DIR, "..", "backend"))
if ENGINE_DIR not in sys.path:
    sys.path.insert(0, ENGINE_DIR)
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from dotenv import load_dotenv
load_dotenv()

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.jobstores.memory import MemoryJobStore

logging.basicConfig(
    level=logging.INFO,
    format="[scheduler] %(asctime)s %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("super_agent_scheduler")


class AgentScheduler:
    def __init__(self):
        self.db_url = os.getenv("DATABASE_URL")
        if not self.db_url:
            raise RuntimeError("DATABASE_URL not set")

        self.scheduler = BackgroundScheduler(
            jobstores={"default": MemoryJobStore()},
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        )
        self._running = True

    def _conn(self):
        return psycopg2.connect(self.db_url, cursor_factory=RealDictCursor)

    def start(self):
        """Load all enabled schedules and start the scheduler."""
        self._load_schedules()
        self.scheduler.start()
        log.info("Scheduler started. Loaded schedules from DB.")

    def stop(self):
        self._running = False
        self.scheduler.shutdown(wait=False)
        log.info("Scheduler stopped.")

    def _load_schedules(self):
        """Load all enabled schedules from DB and register them."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    SELECT s.*, a.status as agent_status, a.user_id
                    FROM agent_schedules s
                    JOIN super_agents a ON a.agent_id = s.agent_id
                    WHERE s.enabled = TRUE AND a.status = 'active'
                """)
                schedules = cur.fetchall()
        finally:
            conn.close()

        for sched in schedules:
            self._register_job(sched)

        log.info(f"Loaded {len(schedules)} active schedules.")

    def _register_job(self, sched):
        """Register a single schedule as an APScheduler job."""
        job_id = f"schedule_{sched['id']}"

        # Remove existing job if any
        existing = self.scheduler.get_job(job_id)
        if existing:
            self.scheduler.remove_job(job_id)

        try:
            parts = sched["cron_expression"].split()
            if len(parts) == 5:
                trigger = CronTrigger(
                    minute=parts[0], hour=parts[1], day=parts[2],
                    month=parts[3], day_of_week=parts[4],
                    timezone=sched.get("timezone", "UTC"),
                )
            else:
                log.warning(f"Invalid cron expression for schedule {sched['id']}: {sched['cron_expression']}")
                return

            self.scheduler.add_job(
                self._execute_task,
                trigger=trigger,
                id=job_id,
                args=[sched["id"], sched["agent_id"], sched["task_prompt"], sched["user_id"]],
                name=sched.get("name") or f"Schedule {sched['id']}",
            )

            # Update next_run_at
            job = self.scheduler.get_job(job_id)
            if job and job.next_run_time:
                conn = self._conn()
                try:
                    with conn.cursor() as cur:
                        cur.execute(
                            "UPDATE agent_schedules SET next_run_at = %s WHERE id = %s",
                            (job.next_run_time, sched["id"])
                        )
                    conn.commit()
                finally:
                    conn.close()

            log.info(f"Registered schedule {sched['id']}: {sched['cron_expression']} -> {sched.get('name', 'unnamed')}")

        except Exception as e:
            log.error(f"Failed to register schedule {sched['id']}: {e}")

    def _execute_task(self, schedule_id, agent_id, task_prompt, user_id):
        """Execute a scheduled task by running the super agent."""
        log.info(f"Executing schedule {schedule_id} for agent {agent_id}")

        try:
            # Check credits first
            from credits import check_and_reserve
            conn = self._conn()
            try:
                has_credits = check_and_reserve(conn, int(user_id))
            finally:
                conn.close()

            if not has_credits:
                log.warning(f"Schedule {schedule_id}: User {user_id} has no credits. Skipping.")
                self._log_schedule_error(schedule_id, agent_id, "Insufficient credits")
                return

            # Create or find a thread for this schedule
            thread_id = self._get_schedule_thread(schedule_id, agent_id)

            # Run the agent
            from super_agent.runner import SuperAgentRunner
            runner = SuperAgentRunner(agent_id, thread_id, self.db_url)
            result = runner.run(
                task_prompt,
                trigger_type="schedule",
                trigger_source=f"schedule_{schedule_id}",
            )

            # Update last_run_at and next_run_at
            conn = self._conn()
            try:
                job = self.scheduler.get_job(f"schedule_{schedule_id}")
                next_run = job.next_run_time if job else None
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE agent_schedules SET last_run_at = NOW(), next_run_at = %s WHERE id = %s",
                        (next_run, schedule_id)
                    )
                conn.commit()
            finally:
                conn.close()

            log.info(f"Schedule {schedule_id} completed. Credits used: {result['credits_used']}")

        except Exception as e:
            log.error(f"Schedule {schedule_id} failed: {e}")
            traceback.print_exc()
            self._log_schedule_error(schedule_id, agent_id, str(e))

    def _get_schedule_thread(self, schedule_id, agent_id):
        """Get or create a dedicated thread for a schedule."""
        thread_id = f"sched{schedule_id}"[:16]
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT thread_id FROM agent_threads WHERE thread_id = %s",
                    (thread_id,)
                )
                if not cur.fetchone():
                    cur.execute(
                        """INSERT INTO agent_threads (thread_id, agent_id, channel, title)
                           VALUES (%s, %s, 'schedule', %s)""",
                        (thread_id, agent_id, f"Schedule {schedule_id}")
                    )
                conn.commit()
        finally:
            conn.close()
        return thread_id

    def _log_schedule_error(self, schedule_id, agent_id, error_msg):
        """Log a schedule execution failure."""
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO agent_logs
                       (agent_id, trigger_type, trigger_source, status, error, completed_at)
                       VALUES (%s, 'schedule', %s, 'failed', %s, NOW())""",
                    (agent_id, f"schedule_{schedule_id}", error_msg[:1000])
                )
            conn.commit()
        finally:
            conn.close()

    def reload_schedules(self):
        """Reload all schedules from DB (call after CRUD operations)."""
        # Remove all existing jobs
        for job in self.scheduler.get_jobs():
            if job.id.startswith("schedule_"):
                self.scheduler.remove_job(job.id)
        self._load_schedules()

    def add_schedule(self, schedule_row):
        """Add a single new schedule."""
        self._register_job(schedule_row)

    def remove_schedule(self, schedule_id):
        """Remove a schedule."""
        job_id = f"schedule_{schedule_id}"
        if self.scheduler.get_job(job_id):
            self.scheduler.remove_job(job_id)


def main():
    """Standalone scheduler worker entry point."""
    log.info("Starting Super Agent Scheduler worker...")

    scheduler = AgentScheduler()
    scheduler.start()

    # Reload schedules every 60 seconds to pick up changes
    def _reload_loop():
        while scheduler._running:
            time.sleep(60)
            try:
                scheduler.reload_schedules()
            except Exception as e:
                log.error(f"Reload error: {e}")

    import threading
    reload_thread = threading.Thread(target=_reload_loop, daemon=True)
    reload_thread.start()

    # Handle graceful shutdown
    def _shutdown(signum, frame):
        log.info("Shutdown signal received.")
        scheduler.stop()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("Scheduler running. Press Ctrl+C to stop.")
    try:
        while scheduler._running:
            time.sleep(1)
    except (KeyboardInterrupt, SystemExit):
        scheduler.stop()


if __name__ == "__main__":
    main()
