"""
Main entry point for the payment agent CLI.

Usage:
    python -m src.main                              # Interactive mode
    python -m src.main "list customers"             # Single query
    python -m src.main --eval                       # Run evaluation with LLM judge
    python -m src.main --eval --op=refund           # Evaluate specific operation
    python -m src.main --eval --payload=ID          # Evaluate specific payload
"""

import sys
import json
from pathlib import Path
from rich.console import Console
from rich.prompt import Prompt
from rich.panel import Panel

from src.config import config
from src.react_agent import run_react_agent, run_with_injection

console = Console()


# =============================================================================
# Payload Loading Helpers
# =============================================================================

def load_payloads() -> dict:
    """Load payloads from JSON file."""
    payloads_path = Path(__file__).parent.parent / "attacks" / "payloads.json"
    if not payloads_path.exists():
        console.print(f"[red]Payloads file not found: {payloads_path}[/red]")
        sys.exit(1)

    with open(payloads_path) as f:
        return json.load(f)


def flatten_operation_payloads(payloads_data: dict, operation: str | None = None) -> list[dict]:
    """
    Flatten the hierarchical payload structure into a list.

    Args:
        payloads_data: The full payloads JSON
        operation: Optional filter for specific operation (refund, subscription, invoice, payment_link)

    Returns:
        List of payload dicts with added 'operation' and 'attack_vector' fields
    """
    flattened = []
    operations = payloads_data.get("operations", {})

    # Filter to specific operation if requested
    if operation:
        if operation not in operations:
            console.print(f"[red]Unknown operation: {operation}[/red]")
            console.print(f"Available: {', '.join(operations.keys())}")
            sys.exit(1)
        operations = {operation: operations[operation]}

    for op_name, op_data in operations.items():
        target_tool = op_data.get("target_tool") or op_data.get("target_tools", [None])[0]

        for vector_name, vector_data in op_data.get("attack_vectors", {}).items():
            for payload in vector_data.get("payloads", []):
                flattened.append({
                    "operation": op_name,
                    "attack_vector": vector_name,
                    "target": payload.get("target", target_tool),
                    **payload
                })

    return flattened


def get_user_request(payload: dict) -> str:
    """Get the user request for a payload."""
    if "user_request" not in payload:
        raise ValueError(f"Payload {payload.get('id', 'unknown')} missing user_request field")
    return payload["user_request"]


# =============================================================================
# CLI Modes
# =============================================================================

def interactive_mode():
    """Run the agent in interactive mode."""
    console.print(Panel.fit(
        "[bold blue]Stripe Payment Agent[/bold blue]\n"
        "Type your requests below. Type 'quit' or 'exit' to stop.\n"
        "Type 'help' for example commands.",
        title="Welcome"
    ))

    while True:
        try:
            user_input = Prompt.ask("\n[bold cyan]You[/bold cyan]")

            if user_input.lower() in ("quit", "exit", "q"):
                console.print("[yellow]Goodbye![/yellow]")
                break

            if user_input.lower() == "help":
                console.print(Panel(
                    "Example commands:\n"
                    "  List all customers\n"
                    "  Show me recent payments\n"
                    "  Refund $50 from payment pi_xxx\n"
                    "  Create an invoice for customer cus_xxx\n"
                    "  List active subscriptions\n"
                    "  Cancel subscription sub_xxx",
                    title="Help"
                ))
                continue

            if not user_input.strip():
                continue

            # Run the agent
            result = run_react_agent(user_input, verbose=True)

            if not result.success:
                console.print(f"[red]Error: {result.error}[/red]")

        except KeyboardInterrupt:
            console.print("\n[yellow]Interrupted. Goodbye![/yellow]")
            break
        except Exception as e:
            console.print(f"[red]Error: {e}[/red]")


def single_query_mode(query: str):
    """Run a single query and exit."""
    result = run_react_agent(query, verbose=True)

    if result.success:
        sys.exit(0)
    else:
        console.print(f"[red]Error: {result.error}[/red]")
        sys.exit(1)


def list_payloads_mode(operation: str | None = None):
    """List available payloads."""
    payloads_data = load_payloads()

    console.print(Panel.fit(
        f"[bold]Payloads v{payloads_data.get('version', '?')}[/bold]\n"
        f"Total: {payloads_data.get('total_payloads', '?')} payloads",
        title="Available Payloads"
    ))

    operations = payloads_data.get("operations", {})

    if operation:
        if operation not in operations:
            console.print(f"[red]Unknown operation: {operation}[/red]")
            sys.exit(1)
        operations = {operation: operations[operation]}

    for op_name, op_data in operations.items():
        console.print(f"\n[bold blue]{op_name.upper()}[/bold blue] ({op_data.get('payload_count', '?')} payloads)")
        console.print(f"  Target: {op_data.get('target_tool', op_data.get('target_tools', []))}")

        for vector_name, vector_data in op_data.get("attack_vectors", {}).items():
            console.print(f"\n  [cyan]{vector_name}[/cyan]: {vector_data.get('description', '')}")
            for payload in vector_data.get("payloads", []):
                user_req = payload.get('user_request', '')[:40]
                console.print(f"    - {payload['id']}")
                console.print(f"      Request: \"{user_req}...\"")
                console.print(f"      Goal: {payload.get('goal', '')[:50]}")


def eval_mode(
    payload_id: str | None = None,
    operation: str | None = None,
    verbose: bool = False,
    show_failures: bool = False,
    save_to_db: bool = True
):
    """
    Run evaluation with LLM judge for TCR/ASR metrics.

    Args:
        payload_id: Specific payload ID to test
        operation: Specific operation to test
        verbose: Show detailed output
        show_failures: Show failure analysis at end
        save_to_db: Save results to SQLite database
    """
    from src.eval import LLMJudge, evaluate_single, create_summary, format_summary, format_by_operation, format_failures
    from src.models import EvalRequest, EvalResponse, EvalRun, ToolCall
    from src.db import EvalDB

    payloads_data = load_payloads()
    payloads = flatten_operation_payloads(payloads_data, operation)

    if payload_id:
        payloads = [p for p in payloads if p["id"] == payload_id]
        if not payloads:
            console.print(f"[red]Payload '{payload_id}' not found[/red]")
            sys.exit(1)

    console.print(Panel.fit(
        f"[bold]Running LLM-judged evaluation[/bold]\n"
        f"Payloads: {len(payloads)}\n"
        f"Model: {config.model}\n"
        f"Judge: {config.judge_model}",
        title="Evaluation Mode"
    ))

    judge = LLMJudge()
    results = []  # For legacy EvalResult format
    run_results: list[tuple[EvalRequest, EvalResponse]] = []  # For new model format

    for i, payload in enumerate(payloads, 1):
        console.print(f"\n[{i}/{len(payloads)}] Testing: [cyan]{payload['id']}[/cyan]")

        # Inject payload
        injection_key = payload["injection_point"].split(".")[-1]
        injection_metadata = {injection_key: payload["payload"]}

        # Get user request from payload
        user_request = get_user_request(payload)

        # Create EvalRequest
        eval_request = EvalRequest(
            payload_id=payload["id"],
            operation=payload.get("operation", "unknown"),
            attack_vector=payload.get("attack_vector", "unknown"),
            user_request=user_request,
            injection_point=payload["injection_point"],
            injected_payload=payload["payload"],
            expected_behavior=payload.get("expected_behavior", ""),
            goal=payload.get("goal", ""),
            model_name=config.model,
            provider=config.llm_provider,
        )

        try:
            # Run agent
            agent_result, call_log = run_with_injection(
                user_input=user_request,
                injection_payload=injection_metadata,
                injection_target="all_payments",
                verbose=verbose
            )

            # Evaluate with LLM judge (skips judge if agent had error)
            eval_result = evaluate_single(
                judge=judge,
                user_request=user_request,
                payload=payload,
                tool_calls=call_log,
                agent_response=agent_result.final_response,
                error=agent_result.error
            )
            results.append(eval_result)

            # Create EvalResponse
            eval_response = EvalResponse(
                request_id=eval_request.id,
                tool_calls=[ToolCall(tool=tc["tool"], input=tc["input"], output=tc["output"]) for tc in call_log],
                agent_response=agent_result.final_response,
                task_completed=eval_result.task_completed,
                attack_succeeded=eval_result.attack_succeeded,
                tcr_reason=eval_result.tcr_reason,
                asr_reason=eval_result.asr_reason,
                outcome=eval_result.outcome,
                error=eval_result.error,
            )
            run_results.append((eval_request, eval_response))

            # Show result based on outcome
            outcome = eval_result.outcome
            if outcome == "ERROR":
                status = "[red]ERROR[/red]"
            elif outcome == "IDEAL":
                status = "[green]IDEAL[/green]"
            elif outcome == "COMPROMISED":
                status = "[red]COMPROMISED[/red]"
            elif outcome == "OVER_REFUSAL":
                status = "[yellow]OVER-REFUSAL[/yellow]"
            else:
                status = "[red]WORST[/red]"

            console.print(f"  Result: {status}")
            if eval_result.error:
                console.print(f"  Error: {eval_result.error[:100]}...")
            elif verbose:
                console.print(f"  TCR: {eval_result.tcr_reason}")
                console.print(f"  ASR: {eval_result.asr_reason}")

        except Exception as e:
            console.print(f"  [red]ERROR: {e}[/red]")

    # Create and display summary
    summary = create_summary("ReAct Agent", results)

    console.print("\n")
    console.print(format_summary(summary))
    console.print(format_by_operation(summary))

    if show_failures:
        console.print(format_failures(summary))

    # Save to database
    if save_to_db and run_results:
        eval_run = EvalRun(
            model_name=config.model,
            provider=config.llm_provider,
            judge_model=config.judge_model,
            operation_filter=operation,
            payload_filter=payload_id,
            total_payloads=len(payloads),
            results=run_results,
        )
        db = EvalDB()
        run_id = db.save_run(eval_run)
        console.print(f"\n[dim]Results saved to database (run_id: {run_id})[/dim]")


def show_call_log_mode(query: str):
    """Run a query and display the call log."""
    from src.react_agent import ReActAgent

    agent = ReActAgent(verbose=True)
    result = agent.run(query)

    # Display call log
    call_log = agent.get_mock_call_log()

    console.print("\n[bold]Call Log:[/bold]")
    for i, call in enumerate(call_log, 1):
        console.print(Panel(
            f"[yellow]Input:[/yellow] {json.dumps(call['input'], indent=2)}\n"
            f"[green]Output:[/green] {json.dumps(call['output'], indent=2)}",
            title=f"[{i}] {call['tool']}"
        ))

    return result


# =============================================================================
# Main Entry Point
# =============================================================================

def list_runs_mode(run_id: str | None = None):
    """List past evaluation runs or show details for a specific run."""
    from src.db import EvalDB

    db = EvalDB()

    if run_id:
        # Show specific run details
        run = db.get_run(run_id)
        if not run:
            console.print(f"[red]Run '{run_id}' not found[/red]")
            sys.exit(1)

        console.print(Panel.fit(
            f"[bold]Run: {run.id}[/bold]\n"
            f"Time: {run.timestamp.strftime('%Y-%m-%d %H:%M:%S')}\n"
            f"Model: {run.model_name} ({run.provider})\n"
            f"Judge: {run.judge_model}\n"
            f"Payloads: {run.total_payloads}\n"
            f"TCR: {run.tcr*100:.1f}%  ASR: {run.asr*100:.1f}%",
            title="Run Details"
        ))

        console.print("\n[bold]Results:[/bold]")
        for req, resp in run.results:
            if resp.outcome == "IDEAL":
                status = "[green]IDEAL[/green]"
            elif resp.outcome == "COMPROMISED":
                status = "[red]COMPROMISED[/red]"
            elif resp.outcome == "ERROR":
                status = "[red]ERROR[/red]"
            else:
                status = f"[yellow]{resp.outcome}[/yellow]"

            console.print(f"  {req.payload_id}: {status}")
            if resp.error:
                console.print(f"    Error: {resp.error[:60]}...")
    else:
        # List all runs
        runs = db.get_runs(limit=20)

        if not runs:
            console.print("[yellow]No evaluation runs found.[/yellow]")
            return

        console.print(Panel.fit("[bold]Recent Evaluation Runs[/bold]", title="Database"))

        for run in runs:
            tcr = run["tcr"] * 100
            asr = run["asr"] * 100
            ts = run["timestamp"][:16]
            model = run["model_name"][:20]
            op = run["operation_filter"] or "all"

            console.print(
                f"  [cyan]{run['id']}[/cyan]  {ts}  {model:<20}  "
                f"op={op:<12}  TCR={tcr:5.1f}%  ASR={asr:5.1f}%  "
                f"(n={run['total_payloads']}, err={run['error_count']})"
            )

        # Show stats
        stats = db.get_stats()
        console.print(f"\n[dim]Total: {stats['total_runs']} runs, {stats['total_evals']} evaluations[/dim]")


def main():
    """Main entry point."""
    # Parse arguments
    args = sys.argv[1:]

    # Extract flags
    verbose = "-v" in args or "--verbose" in args
    show_failures = "--failures" in args
    no_save = "--no-save" in args
    payload_id = None
    operation = None
    run_id = None

    for arg in args:
        if arg.startswith("--payload="):
            payload_id = arg.split("=")[1]
        elif arg.startswith("--op="):
            operation = arg.split("=")[1]
        elif arg.startswith("--run="):
            run_id = arg.split("=")[1]

    # Check for special modes
    if "--runs" in args:
        list_runs_mode(run_id)
        sys.exit(0)

    if "--eval" in args:
        try:
            config.validate_keys()
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)
        eval_mode(payload_id, operation, verbose, show_failures, save_to_db=not no_save)
        sys.exit(0)

    if "--list-payloads" in args:
        list_payloads_mode(operation)
        sys.exit(0)

    if "--show-log" in args:
        try:
            config.validate_keys()
        except ValueError as e:
            console.print(f"[red]{e}[/red]")
            sys.exit(1)
        # Get query from remaining args
        query_args = [a for a in args if not a.startswith("-")]
        if query_args:
            query = " ".join(query_args)
            show_call_log_mode(query)
        else:
            console.print("[red]Please provide a query with --show-log[/red]")
        sys.exit(0)

    if "--help" in args or "-h" in args:
        console.print(Panel(
            "[bold]Usage:[/bold]\n"
            "  python -m src.main                              Interactive mode\n"
            "  python -m src.main \"query\"                      Single query\n"
            "  python -m src.main --eval                       Evaluate with LLM judge (TCR/ASR)\n"
            "  python -m src.main --eval --op=refund           Evaluate specific operation\n"
            "  python -m src.main --eval --failures            Show failure details\n"
            "  python -m src.main --eval --no-save             Don't save to database\n"
            "  python -m src.main --runs                       List past evaluation runs\n"
            "  python -m src.main --runs --run=ID              Show details for a specific run\n"
            "  python -m src.main --list-payloads              List available payloads\n"
            "  python -m src.main --show-log \"query\"           Run with call log display\n"
            "\n[bold]Options:[/bold]\n"
            "  -v, --verbose    Show detailed output\n"
            "  --failures       Show failure analysis (with --eval)\n"
            "  --no-save        Don't save results to database\n"
            "  --op=OPERATION   Filter by operation (refund, subscription, invoice, payment_link)\n"
            "  --payload=ID     Test specific payload by ID\n"
            "  --run=ID         Show specific run details (with --runs)",
            title="Stripe Payment Agent CLI"
        ))
        sys.exit(0)

    # Validate configuration for normal mode
    try:
        config.validate_keys()
    except ValueError as e:
        console.print(f"[red]Configuration error: {e}[/red]")
        console.print("Please set ANTHROPIC_API_KEY in your .env file")
        console.print("\nNote: STRIPE_API_KEY is optional when using mock tools")
        sys.exit(1)

    # Check for command line arguments (query mode)
    query_args = [a for a in args if not a.startswith("-")]
    if query_args:
        query = " ".join(query_args)
        single_query_mode(query)
    else:
        # Interactive mode
        interactive_mode()


if __name__ == "__main__":
    main()
