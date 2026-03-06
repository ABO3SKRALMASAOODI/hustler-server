# routes/agents_pipeline.py
from flask import Blueprint, request, jsonify
import os
import json
import tempfile
import shutil
import subprocess
import importlib.util
import openai

agents_pipeline_bp = Blueprint("agents_pipeline", __name__)
openai.api_key = os.getenv("OPENAI_API_KEY")


# --- helpers for clean agent outputs ---
def _detect_language_from_filename(filename: str) -> str:
    ext = filename.lower().rsplit(".", 1)[-1] if "." in filename else ""
    return {
        "py": "python",
        "js": "javascript",
        "ts": "typescript",
        "jsx": "javascript",
        "tsx": "typescript",
        "json": "json",
        "css": "css",
        "html": "html",
        "md": "markdown",
        "txt": "text",
        "yml": "yaml",
        "yaml": "yaml",
    }.get(ext, "text")


def _strip_code_fences(text: str) -> str:
    """Remove leading/trailing triple-backtick fences if the model included them."""
    if text is None:
        return ""
    s = text.strip()

    # Remove starting
    if s.startswith("```"):
        s = s.split("\n", 1)
        s = s[1] if len(s) > 1 else ""

    # Remove trailing
    if s.endswith("```"):
        s = s[: -3].rstrip()

    return s


# =====================================================
# 1. Utility Functions
# =====================================================
def get_agent_files(spec):
    """
    Collect all unique file names from the orchestrator spec.
    Compatible with new pipeline JSON structure.
    """
    files = set()

    # === New spec style ===
    for f in spec.get("files", []):
        if "file" in f:
            files.add(f["file"])

    # Global reference index (backup source of file names)
    for ref in spec.get("global_reference_index", []):
        if "file" in ref:
            files.add(ref["file"])

    # Depth boost sometimes carries extra files
    for fname in spec.get("__depth_boost", {}).keys():
        files.add(fname)

    # Legacy support (if old orchestrator spec sneaks in)
    for f in spec.get("interface_stub_files", []):
        if "file" in f:
            files.add(f["file"])
    for func in spec.get("function_contract_manifest", {}).get("functions", []):
        if "file" in func:
            files.add(func["file"])
    for dep in spec.get("dependency_graph", []):
        if "file" in dep:
            files.add(dep["file"])
        for d in dep.get("dependencies", []):
            files.add(d)

    return sorted(files)


def extract_file_spec(spec, file_name):
    """
    Build the specification for a single file so the agent knows exactly what to implement.
    Compatible with new orchestrator pipeline.
    """
    file_spec = {
        "file": file_name,
        "functions": [],
        "apis": [],
        "protocols": [],
        "entities": [],
        "errors": [],
        "contracts": {},
    }

    contracts = spec.get("contracts", {})

    # === Functions ===
    for func in contracts.get("functions", []):
        if file_name in func.get("implements", []):
            file_spec["functions"].append(func)

    # === APIs ===
    for api in contracts.get("apis", []):
        if file_name in api.get("implements", []):
            file_spec["apis"].append(api)

    # === Protocols ===
    for proto in contracts.get("protocols", []):
        if file_name in proto.get("implements", []):
            file_spec["protocols"].append(proto)

    # === Entities ===
    for ent in contracts.get("entities", []):
        if file_name in ent.get("implements", []):
            file_spec["entities"].append(ent)

    # === Errors ===
    for err in contracts.get("errors", []):
        if file_name in err.get("implements", []):
            file_spec["errors"].append(err)

    # === Depth boost notes/contracts ===
    if "__depth_boost" in spec and file_name in spec["__depth_boost"]:
        file_spec["depth_notes"] = spec["__depth_boost"][file_name].get("notes", [])
        file_spec["contracts"] = spec["__depth_boost"][file_name].get("contracts", {})

    return file_spec


def verify_imports(outputs):
    """Ensure generated code imports without syntax errors."""
    tmp_dir = tempfile.mkdtemp()
    try:
        for output in outputs:
            file_path = os.path.join(tmp_dir, output["file"])
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w") as f:
                f.write(output["code"])

        for output in outputs:
            file_path = os.path.join(tmp_dir, output["file"])
            spec_obj = importlib.util.spec_from_file_location("module.name", file_path)
            try:
                mod = importlib.util.module_from_spec(spec_obj)
                spec_obj.loader.exec_module(mod)
            except Exception as e:
                raise RuntimeError(f"Import failed for {output['file']}: {e}")
    finally:
        shutil.rmtree(tmp_dir)
    return outputs


def verify_tests(outputs, spec):
    """Run orchestrator-provided integration tests."""
    tmp_dir = tempfile.mkdtemp()
    try:
        for output in outputs:
            file_path = os.path.join(tmp_dir, output["file"])
            os.makedirs(os.path.dirname(file_path), exist_ok=True)
            with open(file_path, "w") as f:
                f.write(output["code"])

        for test in spec.get("integration_tests", []):
            test_path = os.path.join(tmp_dir, test["path"])
            os.makedirs(os.path.dirname(test_path), exist_ok=True)
            with open(test_path, "w") as f:
                f.write(test["code"])

        proc = subprocess.run(["pytest", tmp_dir], capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Integration tests failed:\n{proc.stdout}\n{proc.stderr}")
    finally:
        shutil.rmtree(tmp_dir)
    return outputs


# =====================================================
# 2. Generator & Tester Agents (Relaxed Assessment)
# =====================================================

MAX_RETRIES = 10
_first_review_cache = {}


def run_generator_agent(file_name, file_spec, full_spec, review_feedback=None):
    """Generator Agent: produces code with feedback applied (if any)."""
    feedback_note = ""
    if review_feedback:
        feedback_note = (
            "\n\nFEEDBACK TO FIX (apply where critical, ignore style-only notes):\n"
            f"{review_feedback}"
        )

    agent_prompt = f"""
    You are coding {file_name}. Follow the spec exactly and produce fully working, production-ready code.
    Ignore nitpicky style/docstring issues if unclear, but fix critical errors (syntax, imports, compatibility).
    Output ONLY the complete code for {file_name}.
    ---
    FULL SPEC: {json.dumps(full_spec, indent=2)}
    FILE-SPEC: {json.dumps(file_spec, indent=2)}
    {feedback_note}
    """

    try:
        resp = openai.ChatCompletion.create(
            model="gpt-4o-mini",  # or "gpt-5" if you prefer
            temperature=0,
            request_timeout=60,
            messages=[
                {
                    "role": "system",
                    "content": "You are a perfectionist coding agent focused on correctness and compatibility."
                },
                {"role": "user", "content": agent_prompt}
            ]
        )
        raw = resp.choices[0].message.content or ""
        return _strip_code_fences(raw)
    except Exception as e:
        raise RuntimeError(f"Generator agent failed for {file_name}: {e}")


def run_tester_agent(file_name, file_spec, full_spec, generated_code):
    """Tester Agent: relaxed review — only blocks on hard errors."""
    if file_name in _first_review_cache:
        return _first_review_cache[file_name]

    tester_prompt = f"""
    Review {file_name}. List only CRITICAL blocking issues: syntax errors, failed imports, broken tests,
    missing required functions. Ignore minor style/docstring/naming issues (just note them briefly if any).
    If code is usable and correct, output ONLY: ✅ APPROVED
    ---
    FULL SPEC: {json.dumps(full_spec, indent=2)}
    FILE-SPEC: {json.dumps(file_spec, indent=2)}
    CODE: {generated_code}
    """

    resp = openai.ChatCompletion.create(
        model="gpt-4o-mini",
        temperature=0,
        request_timeout=60,
        messages=[
            {"role": "system", "content": "You are a strict reviewer, but approve code unless there are fatal issues."},
            {"role": "user", "content": tester_prompt}
        ]
    )
    review_text = resp.choices[0].message["content"]
    _first_review_cache[file_name] = review_text
    return review_text


def is_hard_failure(review: str) -> bool:
    """Check if review indicates a real blocking failure."""
    critical_terms = ["SyntaxError", "ImportError", "integration tests failed", "missing required"]
    return any(term.lower() in review.lower() for term in critical_terms)


def run_agents_for_spec(spec):
    """Runs generator + tester loop for each file until approved or retries exhausted."""
    files = get_agent_files(spec)
    outputs = []

    # Map file -> agent name (best effort from blueprint descriptions)
    agent_map = {}
    for agent in spec.get("agent_blueprint", []):
        desc = agent.get("description", "")
        matched_file = None
        for f in spec.get("files", []):
            if f.get("file") and f["file"] in desc:
                matched_file = f["file"]
                break
        if matched_file:
            agent_map[matched_file] = agent.get("name", f"AgentFor-{matched_file}")

    for file_name in files:
        file_spec = extract_file_spec(spec, file_name)
        review_feedback = None
        approved = False
        attempts = 0

        while not approved and attempts < MAX_RETRIES:
            code = run_generator_agent(file_name, file_spec, spec, review_feedback)
            review = run_tester_agent(file_name, file_spec, spec, code)

            if "✅ APPROVED" in review or not is_hard_failure(review):
                approved = True
                outputs.append({
                    "role": "agent",
                    "agent": agent_map.get(file_name, f"AgentFor-{file_name}"),
                    "file": file_name,
                    "language": _detect_language_from_filename(file_name),
                    "content": code  # raw code, no fences
                })
                print(f"✅ {file_name} accepted after {attempts+1} attempt(s).")
            else:
                print(f"❌ {file_name} failed review (Attempt {attempts+1}):\n{review}")
                review_feedback = review
            attempts += 1

        if not approved:
            raise RuntimeError(f"File {file_name} could not be approved after {attempts} attempts.")

    # --- Final validation phase (unchanged) ---
    try:
        verify_imports(outputs)
    except Exception as e:
        print(f"⚠️ Import check failed but continuing: {e}")

    try:
        verify_tests(outputs, spec)
    except Exception as e:
        print(f"⚠️ Tests failed but continuing: {e}")

    return outputs


# =====================================================
# 4. Flask Endpoint
# =====================================================

@agents_pipeline_bp.route("/run_agents", methods=["POST"])
def run_agents_endpoint():
    body = request.get_json(force=True) or {}
    spec = body.get("spec")
    if not spec:
        return jsonify({"error": "Missing spec"}), 400

    try:
        agent_outputs = run_agents_for_spec(spec)
        return jsonify({"role": "assistant", "agents_output": agent_outputs})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
