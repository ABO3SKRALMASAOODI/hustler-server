"""
Data Analyzer skill — analyze CSV, JSON, and structured data.

The agent can parse data, compute statistics, filter/sort rows,
and generate summaries. Turns raw data into actionable insights.
"""

import json
import csv
import io
import statistics
from engine.super_agent.skills.base_skill import BaseSkill


class DataAnalyzerSkill(BaseSkill):
    SKILL_TYPE = "data_analyzer"
    DISPLAY_NAME = "Data Analyzer"
    DESCRIPTION = "Analyze CSV and JSON data — compute statistics, filter rows, find patterns, and generate summaries from structured data."
    CATEGORY = "productivity"
    CONFIG_SCHEMA = {}

    @classmethod
    def get_tool_definitions(cls):
        return [
            {
                "name": "analyze_csv",
                "description": (
                    "Analyze CSV data. Pass raw CSV text and get statistics, summaries, "
                    "or filtered results. Great for analyzing spreadsheet exports, reports, or logs."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "csv_data": {"type": "string", "description": "Raw CSV text (with headers in first row)"},
                        "operation": {
                            "type": "string",
                            "enum": ["summary", "statistics", "filter", "sort", "top_n"],
                            "description": "Operation: summary (overview), statistics (numeric stats), filter (by condition), sort (by column), top_n (top/bottom rows)",
                        },
                        "column": {"type": "string", "description": "Column name for filter/sort/statistics operations"},
                        "condition": {"type": "string", "description": "Filter condition (e.g., '> 100', '== active', 'contains error')"},
                        "limit": {"type": "integer", "description": "Number of rows to return (default 10)"},
                        "ascending": {"type": "boolean", "description": "Sort ascending (default true)"},
                    },
                    "required": ["csv_data", "operation"],
                },
            },
            {
                "name": "analyze_json",
                "description": (
                    "Analyze JSON data — extract fields, compute statistics, "
                    "flatten nested structures, and summarize arrays of objects."
                ),
                "input_schema": {
                    "type": "object",
                    "properties": {
                        "json_data": {"type": "string", "description": "Raw JSON text"},
                        "operation": {
                            "type": "string",
                            "enum": ["summary", "extract", "statistics", "flatten"],
                            "description": "Operation: summary (structure overview), extract (specific path), statistics (numeric fields), flatten (nested to table)",
                        },
                        "path": {"type": "string", "description": "JSON path for extract (e.g., 'data.users', 'results[0].name')"},
                    },
                    "required": ["json_data", "operation"],
                },
            },
        ]

    @classmethod
    def create_handlers(cls, config, context=None):

        def _parse_csv(csv_data):
            reader = csv.DictReader(io.StringIO(csv_data.strip()))
            rows = list(reader)
            headers = reader.fieldnames or []
            return rows, headers

        def _get_numeric_values(rows, column):
            values = []
            for row in rows:
                try:
                    values.append(float(row.get(column, "").replace(",", "")))
                except (ValueError, TypeError):
                    pass
            return values

        def analyze_csv(csv_data, operation, column=None, condition=None, limit=10, ascending=True):
            try:
                rows, headers = _parse_csv(csv_data)
            except Exception as e:
                return f"ERROR: Failed to parse CSV: {str(e)[:200]}"

            if not rows:
                return "ERROR: CSV has no data rows."

            limit = min(max(limit, 1), 100)

            if operation == "summary":
                lines = [f"**CSV Summary:**"]
                lines.append(f"- Rows: {len(rows)}")
                lines.append(f"- Columns: {len(headers)}")
                lines.append(f"- Headers: {', '.join(headers)}")
                lines.append(f"\n**First 3 rows:**")
                for row in rows[:3]:
                    lines.append(f"  {dict(row)}")

                # Detect numeric columns
                numeric_cols = []
                for h in headers:
                    vals = _get_numeric_values(rows, h)
                    if len(vals) > len(rows) * 0.5:
                        numeric_cols.append(h)
                if numeric_cols:
                    lines.append(f"\n**Numeric columns:** {', '.join(numeric_cols)}")
                return "\n".join(lines)

            elif operation == "statistics":
                if not column:
                    return "ERROR: 'column' parameter required for statistics."
                values = _get_numeric_values(rows, column)
                if not values:
                    return f"ERROR: Column '{column}' has no numeric values."

                return (
                    f"**Statistics for '{column}':**\n"
                    f"- Count: {len(values)}\n"
                    f"- Mean: {statistics.mean(values):.2f}\n"
                    f"- Median: {statistics.median(values):.2f}\n"
                    f"- Std Dev: {statistics.stdev(values):.2f if len(values) > 1 else 'N/A'}\n"
                    f"- Min: {min(values)}\n"
                    f"- Max: {max(values)}\n"
                    f"- Sum: {sum(values):.2f}\n"
                )

            elif operation == "filter":
                if not column or not condition:
                    return "ERROR: 'column' and 'condition' required for filter."

                filtered = []
                for row in rows:
                    val = row.get(column, "")
                    try:
                        if condition.startswith(">"):
                            if float(val.replace(",", "")) > float(condition[1:].strip()):
                                filtered.append(row)
                        elif condition.startswith("<"):
                            if float(val.replace(",", "")) < float(condition[1:].strip()):
                                filtered.append(row)
                        elif condition.startswith("=="):
                            if val.strip().lower() == condition[2:].strip().lower():
                                filtered.append(row)
                        elif condition.startswith("!="):
                            if val.strip().lower() != condition[2:].strip().lower():
                                filtered.append(row)
                        elif condition.startswith("contains"):
                            if condition[8:].strip().lower() in val.lower():
                                filtered.append(row)
                        else:
                            if condition.lower() in val.lower():
                                filtered.append(row)
                    except (ValueError, TypeError):
                        pass

                result = f"**Filtered results ({len(filtered)} matches):**\n"
                for row in filtered[:limit]:
                    result += f"  {dict(row)}\n"
                return result

            elif operation == "sort":
                if not column:
                    return "ERROR: 'column' required for sort."

                def sort_key(row):
                    val = row.get(column, "")
                    try:
                        return float(val.replace(",", ""))
                    except (ValueError, TypeError):
                        return val.lower()

                sorted_rows = sorted(rows, key=sort_key, reverse=not ascending)
                result = f"**Sorted by '{column}' ({'asc' if ascending else 'desc'}):**\n"
                for row in sorted_rows[:limit]:
                    result += f"  {dict(row)}\n"
                return result

            elif operation == "top_n":
                if not column:
                    return "ERROR: 'column' required for top_n."
                values_with_rows = []
                for row in rows:
                    try:
                        values_with_rows.append((float(row.get(column, "").replace(",", "")), row))
                    except (ValueError, TypeError):
                        pass

                values_with_rows.sort(key=lambda x: x[0], reverse=True)
                result = f"**Top {limit} by '{column}':**\n"
                for val, row in values_with_rows[:limit]:
                    result += f"  {val} — {dict(row)}\n"
                return result

            return "ERROR: Unknown operation."

        def analyze_json(json_data, operation, path=None):
            try:
                data = json.loads(json_data)
            except json.JSONDecodeError as e:
                return f"ERROR: Invalid JSON: {str(e)[:200]}"

            if operation == "summary":
                def _describe(obj, depth=0, prefix=""):
                    lines = []
                    if isinstance(obj, dict):
                        lines.append(f"{prefix}Object with {len(obj)} keys:")
                        if depth < 3:
                            for k, v in list(obj.items())[:20]:
                                if isinstance(v, (dict, list)):
                                    lines.extend(_describe(v, depth + 1, f"{prefix}  .{k}: "))
                                else:
                                    lines.append(f"{prefix}  .{k}: {type(v).__name__} = {str(v)[:80]}")
                    elif isinstance(obj, list):
                        lines.append(f"{prefix}Array with {len(obj)} items")
                        if obj and depth < 3:
                            lines.extend(_describe(obj[0], depth + 1, f"{prefix}  [0]: "))
                    else:
                        lines.append(f"{prefix}{type(obj).__name__} = {str(obj)[:80]}")
                    return lines

                return "**JSON Structure:**\n" + "\n".join(_describe(data))

            elif operation == "extract":
                if not path:
                    return "ERROR: 'path' required for extract."
                current = data
                for key in path.replace("[", ".").replace("]", "").split("."):
                    if not key:
                        continue
                    if isinstance(current, dict):
                        current = current.get(key)
                    elif isinstance(current, list):
                        try:
                            current = current[int(key)]
                        except (IndexError, ValueError):
                            return f"ERROR: Invalid index '{key}'"
                    else:
                        return f"ERROR: Cannot traverse into {type(current).__name__}"
                    if current is None:
                        return f"ERROR: Path '{path}' not found."

                result = json.dumps(current, indent=2)
                if len(result) > 10000:
                    result = result[:10000] + "\n... [truncated]"
                return result

            elif operation == "statistics":
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    lines = [f"**Array Statistics ({len(data)} items):**\n"]
                    for key in data[0].keys():
                        values = []
                        for item in data:
                            try:
                                values.append(float(item.get(key, "")))
                            except (ValueError, TypeError):
                                pass
                        if len(values) > len(data) * 0.5:
                            lines.append(f"  **{key}:** mean={statistics.mean(values):.2f}, min={min(values)}, max={max(values)}, sum={sum(values):.2f}")
                    return "\n".join(lines)
                return "ERROR: Statistics requires an array of objects."

            elif operation == "flatten":
                if isinstance(data, list) and data and isinstance(data[0], dict):
                    headers = list(data[0].keys())
                    lines = [" | ".join(headers)]
                    lines.append("-" * len(lines[0]))
                    for item in data[:50]:
                        lines.append(" | ".join(str(item.get(h, ""))[:30] for h in headers))
                    return "\n".join(lines)
                return "ERROR: Flatten requires an array of objects."

            return "ERROR: Unknown operation."

        return {"analyze_csv": analyze_csv, "analyze_json": analyze_json}
