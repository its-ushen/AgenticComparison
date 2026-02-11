"""
SQLite database for storing evaluation results.

Usage:
    from src.db import EvalDB

    db = EvalDB()  # Uses default path: data/evals.db
    db.save_run(eval_run)
    runs = db.get_runs()
    run = db.get_run(run_id)
"""

import json
import sqlite3
from pathlib import Path
from datetime import datetime

from src.models import EvalRequest, EvalResponse, EvalRun, ToolCall


DEFAULT_DB_PATH = Path(__file__).parent.parent / "data" / "evals.db"


class EvalDB:
    """SQLite database for evaluation results."""

    def __init__(self, db_path: Path | str | None = None):
        self.db_path = Path(db_path) if db_path else DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self):
        """Initialize database tables."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS eval_runs (
                    id TEXT PRIMARY KEY,
                    timestamp TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    judge_model TEXT NOT NULL,
                    operation_filter TEXT,
                    payload_filter TEXT,
                    total_payloads INTEGER NOT NULL,
                    tcr REAL NOT NULL,
                    asr REAL NOT NULL,
                    error_count INTEGER NOT NULL
                );

                CREATE TABLE IF NOT EXISTS eval_requests (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    attack_vector TEXT NOT NULL,
                    user_request TEXT NOT NULL,
                    injection_point TEXT NOT NULL,
                    injected_payload TEXT NOT NULL,
                    expected_behavior TEXT NOT NULL,
                    goal TEXT NOT NULL,
                    model_name TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES eval_runs(id)
                );

                CREATE TABLE IF NOT EXISTS eval_responses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    request_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    tool_calls TEXT NOT NULL,  -- JSON
                    agent_response TEXT NOT NULL,
                    task_completed INTEGER NOT NULL,
                    attack_succeeded INTEGER NOT NULL,
                    tcr_reason TEXT NOT NULL,
                    asr_reason TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    error TEXT,
                    FOREIGN KEY (request_id) REFERENCES eval_requests(id),
                    FOREIGN KEY (run_id) REFERENCES eval_runs(id)
                );

                CREATE INDEX IF NOT EXISTS idx_requests_run ON eval_requests(run_id);
                CREATE INDEX IF NOT EXISTS idx_responses_run ON eval_responses(run_id);
                CREATE INDEX IF NOT EXISTS idx_responses_outcome ON eval_responses(outcome);
            """)

    def save_run(self, run: EvalRun) -> str:
        """Save a complete evaluation run to the database."""
        with sqlite3.connect(self.db_path) as conn:
            # Insert run
            conn.execute("""
                INSERT INTO eval_runs (id, timestamp, model_name, provider, judge_model,
                                       operation_filter, payload_filter, total_payloads,
                                       tcr, asr, error_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run.id,
                run.timestamp.isoformat(),
                run.model_name,
                run.provider,
                run.judge_model,
                run.operation_filter,
                run.payload_filter,
                run.total_payloads,
                run.tcr,
                run.asr,
                run.error_count,
            ))

            # Insert requests and responses
            for req, resp in run.results:
                conn.execute("""
                    INSERT INTO eval_requests (id, run_id, timestamp, payload_id, operation,
                                               attack_vector, user_request, injection_point,
                                               injected_payload, expected_behavior, goal,
                                               model_name, provider)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    req.id,
                    run.id,
                    req.timestamp.isoformat(),
                    req.payload_id,
                    req.operation,
                    req.attack_vector,
                    req.user_request,
                    req.injection_point,
                    req.injected_payload,
                    req.expected_behavior,
                    req.goal,
                    req.model_name,
                    req.provider,
                ))

                tool_calls_json = json.dumps([tc.model_dump() for tc in resp.tool_calls])
                conn.execute("""
                    INSERT INTO eval_responses (request_id, run_id, timestamp, tool_calls,
                                                agent_response, task_completed, attack_succeeded,
                                                tcr_reason, asr_reason, outcome, error)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    req.id,
                    run.id,
                    resp.timestamp.isoformat(),
                    tool_calls_json,
                    resp.agent_response,
                    int(resp.task_completed),
                    int(resp.attack_succeeded),
                    resp.tcr_reason,
                    resp.asr_reason,
                    resp.outcome,
                    resp.error,
                ))

        return run.id

    def get_runs(self, limit: int = 50) -> list[dict]:
        """Get list of evaluation runs (summary only)."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT id, timestamp, model_name, provider, judge_model,
                       operation_filter, payload_filter, total_payloads,
                       tcr, asr, error_count
                FROM eval_runs
                ORDER BY timestamp DESC
                LIMIT ?
            """, (limit,)).fetchall()

        return [dict(row) for row in rows]

    def get_run(self, run_id: str) -> EvalRun | None:
        """Get a complete evaluation run by ID."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row

            # Get run
            run_row = conn.execute(
                "SELECT * FROM eval_runs WHERE id = ?", (run_id,)
            ).fetchone()

            if not run_row:
                return None

            # Get requests and responses
            requests = conn.execute(
                "SELECT * FROM eval_requests WHERE run_id = ?", (run_id,)
            ).fetchall()

            responses = conn.execute(
                "SELECT * FROM eval_responses WHERE run_id = ?", (run_id,)
            ).fetchall()

            # Build response lookup
            resp_by_req = {r["request_id"]: r for r in responses}

            # Build results
            results = []
            for req_row in requests:
                req = EvalRequest(
                    id=req_row["id"],
                    timestamp=datetime.fromisoformat(req_row["timestamp"]),
                    payload_id=req_row["payload_id"],
                    operation=req_row["operation"],
                    attack_vector=req_row["attack_vector"],
                    user_request=req_row["user_request"],
                    injection_point=req_row["injection_point"],
                    injected_payload=req_row["injected_payload"],
                    expected_behavior=req_row["expected_behavior"],
                    goal=req_row["goal"],
                    model_name=req_row["model_name"],
                    provider=req_row["provider"],
                )

                resp_row = resp_by_req.get(req.id)
                if resp_row:
                    tool_calls = [ToolCall(**tc) for tc in json.loads(resp_row["tool_calls"])]
                    resp = EvalResponse(
                        request_id=req.id,
                        timestamp=datetime.fromisoformat(resp_row["timestamp"]),
                        tool_calls=tool_calls,
                        agent_response=resp_row["agent_response"],
                        task_completed=bool(resp_row["task_completed"]),
                        attack_succeeded=bool(resp_row["attack_succeeded"]),
                        tcr_reason=resp_row["tcr_reason"],
                        asr_reason=resp_row["asr_reason"],
                        outcome=resp_row["outcome"],
                        error=resp_row["error"],
                    )
                    results.append((req, resp))

            return EvalRun(
                id=run_row["id"],
                timestamp=datetime.fromisoformat(run_row["timestamp"]),
                model_name=run_row["model_name"],
                provider=run_row["provider"],
                judge_model=run_row["judge_model"],
                operation_filter=run_row["operation_filter"],
                payload_filter=run_row["payload_filter"],
                total_payloads=run_row["total_payloads"],
                results=results,
            )

    def get_results_by_payload(self, payload_id: str, limit: int = 20) -> list[dict]:
        """Get all results for a specific payload across runs."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT r.run_id, r.timestamp, r.outcome, r.task_completed, r.attack_succeeded,
                       r.tcr_reason, r.asr_reason, e.model_name, e.provider
                FROM eval_responses r
                JOIN eval_requests q ON r.request_id = q.id
                JOIN eval_runs e ON r.run_id = e.id
                WHERE q.payload_id = ?
                ORDER BY r.timestamp DESC
                LIMIT ?
            """, (payload_id, limit)).fetchall()

        return [dict(row) for row in rows]

    def get_stats(self) -> dict:
        """Get overall statistics."""
        with sqlite3.connect(self.db_path) as conn:
            stats = {}

            stats["total_runs"] = conn.execute(
                "SELECT COUNT(*) FROM eval_runs"
            ).fetchone()[0]

            stats["total_evals"] = conn.execute(
                "SELECT COUNT(*) FROM eval_responses"
            ).fetchone()[0]

            # Outcome distribution
            outcomes = conn.execute("""
                SELECT outcome, COUNT(*) as count
                FROM eval_responses
                GROUP BY outcome
            """).fetchall()
            stats["outcomes"] = {row[0]: row[1] for row in outcomes}

            # Average metrics
            metrics = conn.execute("""
                SELECT AVG(tcr) as avg_tcr, AVG(asr) as avg_asr
                FROM eval_runs
            """).fetchone()
            stats["avg_tcr"] = metrics[0] or 0.0
            stats["avg_asr"] = metrics[1] or 0.0

        return stats
