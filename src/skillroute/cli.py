from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

import skillroute
from skillroute.analytics import (
    harness_breakdown,
    library_health,
    parse_since,
    render_stats,
    routing_quality,
)
from skillroute.attribution import resolve_attribution
from skillroute.backends import (
    BACKEND_CHOICES,
    AstraDataAPIBackend,
    AstraDataAPIError,
    RetrievalBackend,
    SqliteFTS5Backend,
    backend_from_name,
)
from skillroute.catalog import Catalog, default_catalog_path
from skillroute.dogfood import discover_default_skill_roots, index_default_skill_roots
from skillroute.evals import run_golden_routes
from skillroute.harness_doctor import (
    DEFAULT_PROBE_TIMEOUT,
    STATUS_FAIL,
    render_doctor_reports,
    run_doctor,
)
from skillroute.harness_render import (
    DEFAULT_SERVER_SOURCE,
    SERVER_SOURCES,
    build_harness_setup,
    default_repo_root,
    render_harness_setup,
)
from skillroute.harness_setup import (
    apply_harness_setup,
    detect_harnesses,
    print_detection_summary,
)
from skillroute.harnesses import PLATFORMS, harness_ids, load_manifests
from skillroute.mcp_setup import (
    CLAUDE_SCOPE_CHOICES,
    MCP_CLIENT_CHOICES,
    build_mcp_setup,
    render_mcp_setup,
)
from skillroute.metadata import (
    default_overlay_path,
    review_metadata_overlay,
    write_metadata_overlay,
)
from skillroute.models import to_jsonable
from skillroute.routing import Router
from skillroute.spec import (
    SPEC_URL,
    render_report_lines,
    report_to_dict,
    summarize_reports,
    validate_target,
)
from skillroute.tuning import tune_weights

# Resolved once at import so argparse `choices` stay in sync with the manifests
# on disk without every subparser reloading them.
HARNESS_IDS = harness_ids()


def main(argv: list[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        args.func(args)
    except Exception as exc:
        if getattr(args, "bridge", False):
            print(json.dumps({"error": {"type": exc.__class__.__name__, "message": str(exc)}}))
            raise SystemExit(1) from exc
        raise


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="skillroute")
    parser.add_argument("--version", action="version", version=f"skillroute {skillroute.__version__}")
    parser.add_argument("--catalog", type=Path, default=None, help="Path to the SQLite catalog")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Index SKILL.md bundles under a root")
    index_parser.add_argument("--root", type=Path, required=True)
    index_parser.add_argument(
        "--strict",
        action="store_true",
        help="Refuse bundles that fail the Agent Skills spec check instead of "
        "indexing them with a warning",
    )
    add_backend_argument(index_parser)
    index_parser.set_defaults(func=cmd_index)

    route_parser = subparsers.add_parser("route", help="Route a request to ranked skills")
    route_parser.add_argument("request")
    route_parser.add_argument("--repo", type=Path, default=None)
    route_parser.add_argument("--limit", type=int, default=5)
    route_parser.add_argument(
        "--harness",
        default=None,
        help="Attribute this route to a harness (defaults to $SKILLROUTE_HARNESS)",
    )
    add_backend_argument(route_parser)
    route_parser.add_argument("--json", action="store_true", dest="as_json")
    route_parser.set_defaults(func=cmd_route)

    search_parser = subparsers.add_parser("search", help="Search indexed skills")
    search_parser.add_argument("query")
    search_parser.add_argument("--limit", type=int, default=10)
    add_backend_argument(search_parser)
    search_parser.add_argument("--json", action="store_true", dest="as_json")
    search_parser.set_defaults(func=cmd_search)

    inspect_parser = subparsers.add_parser("inspect", help="Inspect one skill by id or name")
    inspect_parser.add_argument("skill_id")
    inspect_parser.add_argument("--json", action="store_true", dest="as_json")
    inspect_parser.set_defaults(func=cmd_inspect)

    validate_parser = subparsers.add_parser(
        "validate",
        help="Check SKILL.md bundles against the Agent Skills spec (agentskills.io)",
    )
    validate_parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        metavar="PATH",
        help="SKILL.md files, bundle directories, or roots to scan (default: .)",
    )
    validate_parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail on warnings as well as errors",
    )
    validate_parser.add_argument("--json", action="store_true", dest="as_json")
    validate_parser.set_defaults(func=cmd_validate)

    eval_parser = subparsers.add_parser("eval", help="Run eval commands")
    eval_subparsers = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_run_parser = eval_subparsers.add_parser("run", help="Run golden route evals")
    eval_run_parser.add_argument("--cases", type=Path, required=True)
    eval_run_parser.add_argument(
        "--index-root",
        type=Path,
        action="append",
        default=[],
        help="Index a skill root before running evals. Can be passed multiple times.",
    )
    eval_run_parser.add_argument(
        "--fresh",
        action="store_true",
        help="Run evals against a temporary isolated catalog.",
    )
    add_backend_argument(eval_run_parser)
    eval_run_parser.add_argument("--json", action="store_true", dest="as_json")
    eval_run_parser.set_defaults(func=cmd_eval_run)
    eval_tune_parser = eval_subparsers.add_parser(
        "tune",
        help="Grid-search routing weights against golden route cases",
    )
    eval_tune_parser.add_argument("--cases", type=Path, required=True)
    eval_tune_parser.add_argument(
        "--index-root",
        type=Path,
        action="append",
        default=[],
        help="Index a skill root before tuning. Can be passed multiple times.",
    )
    eval_tune_parser.add_argument(
        "--fresh",
        action="store_true",
        help="Tune against a temporary isolated catalog.",
    )
    eval_tune_parser.add_argument(
        "--step",
        type=float,
        default=0.2,
        help=(
            "Blend weight grid step in (0, 1]. Smaller explores more combinations. "
            "Snapped to the nearest 1/N so blends stay on the unit simplex."
        ),
    )
    eval_tune_parser.add_argument(
        "--top",
        type=int,
        default=5,
        help="How many best weight sets to print.",
    )
    add_backend_argument(eval_tune_parser)
    eval_tune_parser.add_argument("--json", action="store_true", dest="as_json")
    eval_tune_parser.set_defaults(func=cmd_eval_tune)

    dogfood_parser = subparsers.add_parser("dogfood", help="Dogfood SkillRoute against local skill roots")
    dogfood_subparsers = dogfood_parser.add_subparsers(dest="dogfood_command", required=True)
    dogfood_roots_parser = dogfood_subparsers.add_parser("roots", help="List discoverable local skill roots")
    dogfood_roots_parser.add_argument("--home", type=Path, default=None)
    dogfood_roots_parser.add_argument("--json", action="store_true", dest="as_json")
    dogfood_roots_parser.set_defaults(func=cmd_dogfood_roots)
    dogfood_index_parser = dogfood_subparsers.add_parser("index", help="Index discoverable local skill roots")
    dogfood_index_parser.add_argument("--home", type=Path, default=None)
    dogfood_index_parser.add_argument("--json", action="store_true", dest="as_json")
    dogfood_index_parser.set_defaults(func=cmd_dogfood_index)

    metadata_parser = subparsers.add_parser("metadata", help="Create and review skill metadata overlays")
    metadata_subparsers = metadata_parser.add_subparsers(dest="metadata_command", required=True)
    metadata_suggest_parser = metadata_subparsers.add_parser(
        "suggest",
        help="Write reviewable metadata suggestions as an overlay JSON file",
    )
    metadata_suggest_parser.add_argument("--root", type=Path, required=True)
    metadata_suggest_parser.add_argument("--output", type=Path, default=None)
    metadata_suggest_parser.add_argument("--force", action="store_true")
    metadata_suggest_parser.add_argument("--json", action="store_true", dest="as_json")
    metadata_suggest_parser.set_defaults(func=cmd_metadata_suggest)
    metadata_review_parser = metadata_subparsers.add_parser(
        "review",
        help="Validate and summarize a metadata overlay JSON file",
    )
    metadata_review_parser.add_argument("--root", type=Path, default=None)
    metadata_review_parser.add_argument("--overlay", type=Path, default=None)
    metadata_review_parser.add_argument("--json", action="store_true", dest="as_json")
    metadata_review_parser.set_defaults(func=cmd_metadata_review)

    traces_parser = subparsers.add_parser("traces", help="Inspect recorded route traces")
    traces_subparsers = traces_parser.add_subparsers(dest="traces_command", required=True)
    traces_list_parser = traces_subparsers.add_parser("list", help="List recent route traces")
    traces_list_parser.add_argument("--limit", type=int, default=20)
    traces_list_parser.add_argument("--json", action="store_true", dest="as_json")
    traces_list_parser.set_defaults(func=cmd_traces_list)
    traces_show_parser = traces_subparsers.add_parser("show", help="Show one route trace")
    traces_show_parser.add_argument("trace_id", type=int)
    traces_show_parser.add_argument("--json", action="store_true", dest="as_json")
    traces_show_parser.set_defaults(func=cmd_traces_show)

    backend_parser = subparsers.add_parser("backend", help="Work with external retrieval backends")
    backend_subparsers = backend_parser.add_subparsers(dest="backend_command", required=True)
    backend_status_parser = backend_subparsers.add_parser("status", help="Show selected retrieval backend status")
    add_backend_argument(backend_status_parser)
    backend_status_parser.add_argument("--json", action="store_true", dest="as_json")
    backend_status_parser.set_defaults(func=cmd_backend_status)
    astra_parser = backend_subparsers.add_parser("astra", help="Use Astra DB Data API as a retrieval backend")
    astra_subparsers = astra_parser.add_subparsers(dest="astra_command", required=True)
    astra_create_parser = astra_subparsers.add_parser(
        "create-collection",
        help="Create the configured Astra collection",
    )
    astra_create_parser.add_argument(
        "--options-json",
        default=None,
        help="Optional JSON object passed as createCollection.options.",
    )
    astra_create_parser.add_argument("--json", action="store_true", dest="as_json")
    astra_create_parser.set_defaults(func=cmd_backend_astra_create_collection)
    astra_upsert_parser = astra_subparsers.add_parser(
        "upsert",
        help="Upsert indexed catalog skills into the configured Astra collection",
    )
    astra_upsert_parser.add_argument(
        "--include-refs",
        action="store_true",
        help="Include per-skill backend refs in JSON output.",
    )
    astra_upsert_parser.add_argument("--json", action="store_true", dest="as_json")
    astra_upsert_parser.set_defaults(func=cmd_backend_astra_upsert)
    astra_search_parser = astra_subparsers.add_parser(
        "search",
        help="Search the configured Astra collection with vectorize.",
    )
    astra_search_parser.add_argument("query")
    astra_search_parser.add_argument("--limit", type=int, default=10)
    astra_search_parser.add_argument("--json", action="store_true", dest="as_json")
    astra_search_parser.set_defaults(func=cmd_backend_astra_search)

    mcp_parser = subparsers.add_parser("mcp", help="Generate MCP client setup")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command", required=True)
    mcp_config_parser = mcp_subparsers.add_parser(
        "config",
        help="Print SkillRoute MCP setup for supported agent clients",
    )
    mcp_config_parser.add_argument("--client", choices=MCP_CLIENT_CHOICES, required=True)
    mcp_config_parser.add_argument("--repo-root", type=Path, default=None)
    mcp_config_parser.add_argument(
        "--catalog",
        type=Path,
        dest="mcp_catalog",
        default=None,
        help="Catalog path for generated MCP env. Can also be passed globally before mcp.",
    )
    add_backend_argument(mcp_config_parser)
    mcp_config_parser.add_argument("--server-name", default="skillroute")
    mcp_config_parser.add_argument(
        "--scope",
        choices=CLAUDE_SCOPE_CHOICES,
        default="user",
        help="Claude Code scope for generated claude mcp add commands.",
    )
    mcp_config_parser.add_argument("--json", action="store_true", dest="as_json")
    mcp_config_parser.set_defaults(func=cmd_mcp_config)

    harness_parser = subparsers.add_parser(
        "harness", help="Inspect and configure agent harnesses"
    )
    harness_subparsers = harness_parser.add_subparsers(dest="harness_command", required=True)

    harness_list_parser = harness_subparsers.add_parser(
        "list", help="List every harness SkillRoute knows about"
    )
    harness_list_parser.add_argument("--mode", default=None, help="Only harnesses supporting MODE")
    harness_list_parser.add_argument("--json", action="store_true", dest="as_json")
    harness_list_parser.set_defaults(func=cmd_harness_list)

    harness_detect_parser = harness_subparsers.add_parser(
        "detect", help="Detect which harnesses are installed"
    )
    harness_detect_parser.add_argument("--json", action="store_true", dest="as_json")
    harness_detect_parser.set_defaults(func=cmd_harness_detect)

    harness_show_parser = harness_subparsers.add_parser(
        "show", help="Show the setup SkillRoute would apply for one harness"
    )
    harness_show_parser.add_argument("harness", choices=HARNESS_IDS)
    harness_show_parser.add_argument("--mode", default="mcp")
    harness_show_parser.add_argument("--scope", default=None)
    harness_show_parser.add_argument("--repo-root", type=Path, default=None)
    harness_show_parser.add_argument("--server-name", default="skillroute")
    harness_show_parser.add_argument(
        "--platform", choices=PLATFORMS, default=None, help="Render for another platform"
    )
    add_backend_argument(harness_show_parser)
    harness_show_parser.add_argument("--json", action="store_true", dest="as_json")
    harness_show_parser.add_argument(
        "--server-source",
        choices=SERVER_SOURCES,
        default=DEFAULT_SERVER_SOURCE,
        help="Auto-detect a checkout, or select local / the published npm package (npx)",
    )
    harness_show_parser.set_defaults(func=cmd_harness_show)

    harness_install_parser = harness_subparsers.add_parser(
        "install", help="Configure a harness to use SkillRoute"
    )
    harness_install_parser.add_argument("harness", choices=HARNESS_IDS)
    harness_install_parser.add_argument("--mode", default="mcp")
    harness_install_parser.add_argument("--scope", default=None)
    harness_install_parser.add_argument("--repo-root", type=Path, default=None)
    harness_install_parser.add_argument("--server-name", default="skillroute")
    add_backend_argument(harness_install_parser)
    harness_install_parser.add_argument(
        "--dry-run", action="store_true", help="Print what would be done, change nothing"
    )
    harness_install_parser.add_argument("--yes", action="store_true")
    harness_install_parser.add_argument(
        "--server-source",
        choices=SERVER_SOURCES,
        default=DEFAULT_SERVER_SOURCE,
        help="Auto-detect a checkout, or select local / the published npm package (npx)",
    )
    harness_install_parser.set_defaults(func=cmd_harness_install)

    harness_doctor_parser = harness_subparsers.add_parser(
        "doctor", help="Verify harness packs still match reality"
    )
    # No `choices=`: with nargs="*" argparse renders the empty-list default into
    # the usage line. run_doctor() validates and names the valid ids instead.
    harness_doctor_parser.add_argument(
        "harness",
        nargs="*",
        metavar="HARNESS",
        help=f"Harnesses to check (default: all). One of: {', '.join(HARNESS_IDS)}",
    )
    harness_doctor_parser.add_argument("--mode", default="mcp")
    harness_doctor_parser.add_argument("--repo-root", type=Path, default=None)
    harness_doctor_parser.add_argument("--server-name", default="skillroute")
    add_backend_argument(harness_doctor_parser)
    harness_doctor_parser.add_argument(
        "--no-probe",
        action="store_true",
        help="Skip running the configured server; check the pack statically only",
    )
    harness_doctor_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_PROBE_TIMEOUT,
        help="Seconds to wait for the server to answer initialize",
    )
    harness_doctor_parser.add_argument("--json", action="store_true", dest="as_json")
    harness_doctor_parser.set_defaults(func=cmd_harness_doctor)

    stats_parser = subparsers.add_parser(
        "stats", help="Report on routing quality and skill-library health"
    )
    stats_parser.add_argument(
        "--since",
        default=None,
        help="Only count routes since a span (30d, 12h, 2w) or an ISO date",
    )
    stats_parser.add_argument(
        "--harness", default=None, help="Only count routes from this harness"
    )
    stats_parser.add_argument(
        "--limit", type=int, default=10, help="How many skills to list per section"
    )
    stats_parser.add_argument("--json", action="store_true", dest="as_json")
    stats_parser.set_defaults(func=cmd_stats)

    ui_parser = subparsers.add_parser("ui", help="Launch the local Skill Atlas web UI")
    ui_parser.add_argument("--host", default="127.0.0.1")
    ui_parser.add_argument("--port", type=int, default=8765)
    ui_parser.add_argument("--no-open", action="store_true", help="Do not open a browser automatically.")
    ui_parser.set_defaults(func=cmd_ui)

    bridge_parser = subparsers.add_parser("bridge", help="JSON stdin/stdout bridge for MCP wrappers")
    bridge_parser.add_argument("operation", choices=["route", "search", "inspect"])
    bridge_parser.set_defaults(func=cmd_bridge, bridge=True)

    return parser


def add_backend_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--backend",
        choices=BACKEND_CHOICES,
        default=None,
        help="Retrieval backend for route/search. Defaults to SKILLROUTE_BACKEND or local.",
    )


def catalog_from_args(args: argparse.Namespace) -> Catalog:
    return Catalog(args.catalog or default_catalog_path())


def backend_from_args(args: argparse.Namespace) -> RetrievalBackend:
    try:
        return backend_from_name(getattr(args, "backend", None))
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc


def router_from_args(catalog: Catalog, args: argparse.Namespace) -> Router:
    """Build a Router, reporting a bad SKILLROUTE_WEIGHTS the way bad flags are."""
    try:
        return Router(catalog, backend=backend_from_args(args))
    except (TypeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc


def cmd_index(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    backend = backend_from_args(args)
    skills = catalog.index_root(args.root, spec_strict=args.strict)
    print(f"Indexed {len(skills)} skills into {catalog.path}")
    refs: list[dict[str, Any]]
    if isinstance(backend, SqliteFTS5Backend):
        # FTS5 indexes itself at search time; refs only record catalog coverage.
        refs = backend.upsert_skills(skills)
    elif backend.name != "local-token":
        refs = run_astra_command(lambda: backend.upsert_skills(skills))
    else:
        return
    for ref in refs:
        catalog.save_backend_ref(
            ref["skill_id"], ref["backend"], ref["ref"], ref.get("status", "indexed")
        )
    statuses = count_ref_statuses(refs)
    print(f"Wrote {len(refs)} {backend.name} refs: {json.dumps(statuses, sort_keys=True)}")


def cmd_route(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    response = router_from_args(catalog, args).route(
        args.request,
        repo=args.repo,
        limit=args.limit,
        attribution=resolve_attribution(
            explicit=getattr(args, "harness", None), surface="cli"
        ),
    )
    if args.as_json:
        print_json(response)
        return
    print_route(response)


def cmd_search(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    rows = router_from_args(catalog, args).search(args.query, limit=args.limit)
    if args.as_json:
        print_json(rows)
        return
    if not rows:
        print("No matching skills.")
        return
    for row in rows:
        print(f"{row['name']} ({row['skill_id']}) score={row['score']}")
        print(f"  {row['description']}")
        for snippet in row["evidence"]:
            print(f"  evidence: {snippet}")


def cmd_inspect(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    skill = catalog.get_skill(args.skill_id)
    if skill is None:
        raise SystemExit(f"Skill not found: {args.skill_id}")
    payload = to_jsonable(skill)
    payload["backend_refs"] = catalog.backend_refs(skill.id)
    if args.as_json:
        print_json(payload)
        return
    print(f"{skill.name} ({skill.id})")
    print(skill.description)
    print(f"path: {skill.skill_path}")
    for spec_field in ("license", "compatibility", "allowed-tools"):
        value = skill.metadata.get(spec_field)
        if value:
            print(f"{spec_field}: {value}")
    if isinstance(skill.metadata.get("metadata"), dict):
        print(f"metadata: {json.dumps(skill.metadata['metadata'], sort_keys=True)}")
    if skill.tags:
        print(f"tags: {', '.join(skill.tags)}")
    if skill.facets:
        print(f"facets: {json.dumps(skill.facets, sort_keys=True)}")
    if skill.relationships:
        print("relationships:")
        for relationship in skill.relationships:
            print(f"  {relationship.type}: {relationship.target}")
    if skill.excerpts:
        print("excerpts:")
        for excerpt in skill.excerpts:
            print(f"  [{excerpt.kind}] {excerpt.text}")


def cmd_validate(args: argparse.Namespace) -> None:
    paths = args.paths or [Path(".")]
    reports = []
    for path in paths:
        reports.extend(validate_target(path))
    summary = summarize_reports(reports)
    if args.as_json:
        print_json(
            {
                "spec": SPEC_URL,
                "summary": summary,
                "reports": [report_to_dict(report) for report in reports],
            }
        )
    else:
        if not reports:
            print(f"No SKILL.md bundles found under: {', '.join(str(path) for path in paths)}")
        for report in reports:
            lines = render_report_lines(report)
            if lines:
                print(report.skill_path)
                for line in lines:
                    print(line)
        print(
            f"Spec check ({SPEC_URL}): {summary['bundles']} bundles, "
            f"{summary['errors']} errors, {summary['warnings']} warnings"
        )
    # Non-zero exit makes validate usable as a CI gate; --strict also fails
    # on warnings, for libraries that want the recommendations enforced.
    if summary["errors"] or (args.strict and summary["warnings"]):
        raise SystemExit(1)


def cmd_eval_run(args: argparse.Namespace) -> None:
    context = TemporaryDirectory() if args.fresh else nullcontext(None)
    with context as temp_dir:
        catalog = Catalog(Path(temp_dir) / "catalog.db") if temp_dir else catalog_from_args(args)
        for root in args.index_root:
            catalog.index_root(root)
        try:
            results = run_golden_routes(router_from_args(catalog, args), args.cases)
        except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise SystemExit(f"Could not run eval cases from {args.cases}: {exc}") from exc
    if args.as_json:
        print_json(results)
        return
    passed = sum(1 for result in results if result.passed)
    print(f"{passed}/{len(results)} golden route cases passed")
    for result in results:
        status = "PASS" if result.passed else "FAIL"
        print(f"{status} {result.name}")
        for note in result.notes:
            print(f"  {note}")
    if passed != len(results):
        raise SystemExit(1)


def cmd_eval_tune(args: argparse.Namespace) -> None:
    context = TemporaryDirectory() if args.fresh else nullcontext(None)
    with context as temp_dir:
        catalog = Catalog(Path(temp_dir) / "catalog.db") if temp_dir else catalog_from_args(args)
        for root in args.index_root:
            catalog.index_root(root)
        try:
            results = tune_weights(
                catalog,
                args.cases,
                backend=backend_from_args(args),
                step=args.step,
            )
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
            raise SystemExit(f"Could not tune weights from {args.cases}: {exc}") from exc
    top = max(1, args.top)
    if args.as_json:
        print_json([result.to_json() for result in results[:top]])
        return
    if not results:
        print("No eval cases to tune against.")
        return
    print(f"Top {min(top, len(results))} weight sets ({results[0].total} cases):")
    for rank, result in enumerate(results[:top], start=1):
        print(f"{rank}. score={result.score:.4f} passed={result.passed}/{result.total}")
        print(f"   mrr={result.mean_reciprocal_rank:.4f} clarification={result.clarification_accuracy:.4f}")
        print(f"   weights: {json.dumps(result.weights, sort_keys=True)}")
    best = results[0]
    print("\nApply with: SKILLROUTE_WEIGHTS='" + json.dumps(best.weights, sort_keys=True) + "'")


def cmd_dogfood_roots(args: argparse.Namespace) -> None:
    roots = discover_default_skill_roots(args.home)
    payload = [
        {"path": str(root.path), "skill_count": root.skill_count}
        for root in roots
    ]
    if args.as_json:
        print_json(payload)
        return
    if not roots:
        print("No default skill roots found.")
        return
    for root in roots:
        print(f"{root.path} ({root.skill_count} skills)")


def cmd_dogfood_index(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    result = index_default_skill_roots(catalog, home=args.home)
    payload = {
        "catalog": str(catalog.path),
        "indexed_count": result.indexed_count,
        "roots": [
            {"path": str(root.path), "skill_count": root.skill_count}
            for root in result.roots
        ],
    }
    if args.as_json:
        print_json(payload)
        return
    if not result.roots:
        print("No default skill roots found.")
        return
    print(f"Indexed {result.indexed_count} skills into {catalog.path}")
    for root in result.roots:
        print(f"- {root.path} ({root.skill_count} skills)")


def cmd_metadata_suggest(args: argparse.Namespace) -> None:
    try:
        result = write_metadata_overlay(args.root, output=args.output, force=args.force)
    except FileExistsError as exc:
        raise SystemExit(str(exc)) from exc
    payload = {"output_path": str(result.output_path), "skill_count": result.skill_count}
    if args.as_json:
        print_json(payload)
        return
    print(f"Wrote metadata suggestions for {result.skill_count} skills to {result.output_path}")


def cmd_metadata_review(args: argparse.Namespace) -> None:
    if args.overlay is None and args.root is None:
        raise SystemExit("Provide --overlay or --root.")
    overlay_path = args.overlay or default_overlay_path(args.root)
    result = review_metadata_overlay(overlay_path)
    payload = to_jsonable(result)
    payload["overlay_path"] = str(result.overlay_path)
    if args.as_json:
        print_json(payload)
        return
    print(f"Overlay: {result.overlay_path}")
    print(f"Skills: {result.skill_count}")
    print(f"Relationships: {result.relationship_count}")
    if result.status_counts:
        print(f"Review status: {json.dumps(result.status_counts, sort_keys=True)}")
    if result.issues:
        print("Issues:")
        for issue in result.issues:
            print(f"- {issue}")
        raise SystemExit(1)
    print("No validation issues.")


def cmd_traces_list(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    traces = catalog.list_route_traces(limit=args.limit)
    if args.as_json:
        print_json(traces)
        return
    if not traces:
        print("No route traces recorded.")
        return
    for trace in traces:
        top = trace["top_candidate"]
        top_text = f"{top['name']} confidence={top['confidence']}" if top else "no candidates"
        request_text = trace["request"].get("request", "")
        print(f"{trace['id']} {trace['created_at']} backend={trace.get('backend') or 'unknown'}")
        print(f"  request: {request_text}")
        print(f"  top: {top_text}")
        print(f"  clarification_needed: {trace['clarification_needed']}")


def cmd_traces_show(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    trace = catalog.get_route_trace(args.trace_id)
    if trace is None:
        raise SystemExit(f"Route trace not found: {args.trace_id}")
    if args.as_json:
        print_json(trace)
        return
    print_trace(trace)


def cmd_backend_status(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    backend = backend_from_args(args)
    payload = backend_status_payload(catalog, backend)
    if args.as_json:
        print_json(payload)
        return
    print_backend_status(payload)


def cmd_backend_astra_create_collection(args: argparse.Namespace) -> None:
    backend = AstraDataAPIBackend.from_env()
    options = parse_options_json(args.options_json)
    result = run_astra_command(lambda: backend.create_collection(options))
    if args.as_json:
        print_json(result)
        return
    print("Astra collection create command completed.")


def cmd_backend_astra_upsert(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    backend = AstraDataAPIBackend.from_env()
    skills = catalog.list_skills()
    refs = run_astra_command(lambda: backend.upsert_skills(skills))
    for ref in refs:
        catalog.save_backend_ref(ref["skill_id"], ref["backend"], ref["ref"], ref.get("status", "indexed"))
    statuses = count_ref_statuses(refs)
    payload = {
        "backend": backend.name,
        "collection": backend.collection,
        "keyspace": backend.keyspace,
        "skill_count": len(skills),
        "ref_count": len(refs),
        "status_counts": statuses,
    }
    if args.include_refs:
        payload["refs"] = refs
    if args.as_json:
        print_json(payload)
        return
    print(f"Astra upsert processed {len(refs)} skills: {json.dumps(statuses, sort_keys=True)}")


def cmd_backend_astra_search(args: argparse.Namespace) -> None:
    catalog = catalog_from_args(args)
    backend = AstraDataAPIBackend.from_env()
    rows = run_astra_command(lambda: backend.search(args.query, catalog.list_skills(), limit=args.limit))
    if args.as_json:
        print_json(rows)
        return
    if not rows:
        print("No Astra results.")
        return
    for row in rows:
        skill = catalog.get_skill(row["skill_id"])
        name = skill.name if skill else row["skill_id"]
        print(f"{name} ({row['skill_id']}) score={row['score']}")


def cmd_mcp_config(args: argparse.Namespace) -> None:
    # Deprecation goes to stderr so `--json` stdout stays parseable.
    print(
        "skillroute: `mcp config --client` is deprecated; use "
        f"`skillroute harness show {args.client}` (or `harness install`). "
        "It will be removed in 0.3.",
        file=sys.stderr,
    )
    payload = build_mcp_setup(
        client=args.client,
        repo_root=args.repo_root,
        catalog=args.mcp_catalog or args.catalog,
        backend=args.backend,
        server_name=args.server_name,
        claude_scope=args.scope,
    )
    if args.as_json:
        print_json(payload)
        return
    print(render_mcp_setup(payload))


def cmd_harness_list(args: argparse.Namespace) -> None:
    manifests = [
        manifest
        for manifest in load_manifests().values()
        if not args.mode or manifest.supports(args.mode)
    ]
    if args.as_json:
        print_json(
            [
                {
                    "id": manifest.id,
                    "name": manifest.display_name,
                    "tier": manifest.tier,
                    "homepage": manifest.homepage,
                    "modes": sorted(manifest.modes),
                }
                for manifest in manifests
            ]
        )
        return
    if not manifests:
        print(f"No harnesses support mode {args.mode!r}.")
        return
    width = max(len(manifest.id) for manifest in manifests)
    for manifest in manifests:
        modes = " ".join(sorted(manifest.modes))
        print(f"{manifest.id:<{width}}  {manifest.tier:<12}  {modes}")


def cmd_harness_detect(args: argparse.Namespace) -> None:
    detections = detect_harnesses()
    if args.as_json:
        print_json([asdict(detection) for detection in detections])
        return
    print_detection_summary(detections)


def cmd_harness_show(args: argparse.Namespace) -> None:
    payload = build_harness_setup(
        harness=args.harness,
        mode=args.mode,
        repo_root=args.repo_root,
        catalog=args.catalog,
        backend=args.backend,
        server_name=args.server_name,
        scope=args.scope,
        platform=args.platform,
        server_source=args.server_source,
    )
    if args.as_json:
        print_json(payload)
        return
    print(render_harness_setup(payload))


def cmd_harness_install(args: argparse.Namespace) -> None:
    repo_root = (args.repo_root or default_repo_root()).expanduser().resolve()
    payload = build_harness_setup(
        harness=args.harness,
        mode=args.mode,
        repo_root=repo_root,
        catalog=args.catalog,
        backend=args.backend,
        server_name=args.server_name,
        scope=args.scope,
        server_source=args.server_source,
    )
    if args.dry_run:
        print(render_harness_setup(payload))
        return
    detection = next(
        (item for item in detect_harnesses() if item.id == args.harness), None
    )
    if detection is None:
        raise SystemExit(f"Unknown harness: {args.harness}")
    result = apply_harness_setup(
        detection,
        repo_root=repo_root,
        catalog=args.catalog,
        backend=args.backend,
        server_name=args.server_name,
        mode="1" if args.yes else "prompt",
        yes=args.yes,
        install_mode=args.mode,
        server_source=payload["server_source"],
        scope=payload.get("scope"),
    )
    print(f"{detection.name}: {result.status} - {result.message}")
    if result.backup_path:
        print(f"{detection.name}: backup - {result.backup_path}")


def cmd_harness_doctor(args: argparse.Namespace) -> None:
    try:
        reports = run_doctor(
            args.harness or None,
            repo_root=args.repo_root,
            catalog=args.catalog,
            backend=args.backend,
            server_name=args.server_name,
            mode=args.mode,
            probe=not args.no_probe,
            timeout=args.timeout,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.as_json:
        print_json([report.to_dict() for report in reports])
    else:
        print(render_doctor_reports(reports))
    # Non-zero exit so `harness doctor` is usable as a CI gate.
    if any(report.status == STATUS_FAIL for report in reports):
        raise SystemExit(1)


def cmd_stats(args: argparse.Namespace) -> None:
    try:
        since = parse_since(args.since)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    catalog = catalog_from_args(args)
    # Running `stats` before ever indexing is a reasonable thing to do, and the
    # answer is "no data yet" rather than a SQL error about a missing table.
    catalog.initialize()
    health = library_health(catalog, since=since, harness=args.harness, limit=args.limit)
    quality = routing_quality(catalog, since=since, harness=args.harness)
    harnesses = harness_breakdown(catalog, since=since)
    if args.as_json:
        print_json(
            {
                "since": since,
                "harness": args.harness,
                "library": health.to_dict(),
                "quality": quality.to_dict(),
                "harnesses": [item.to_dict() for item in harnesses],
            }
        )
        return
    print(render_stats(health, quality, harnesses, since=args.since))


UI_EXTRA_MISSING = (
    "The Skill Atlas UI needs the `ui` extra, which is not installed.\n"
    "  uv:   uv pip install 'skillroute[ui]'\n"
    "  pip:  pip install 'skillroute[ui]'\n"
    "  uvx:  uvx --from 'skillroute[ui]' skillroute ui\n"
    "Everything else (route, index, search, stats, harness) needs no extras."
)


def cmd_ui(args: argparse.Namespace) -> None:
    # Imported here, not at module scope, so the whole CLI does not depend on
    # fastapi being installed. A missing extra should read as a missing extra,
    # not as a traceback about a module nobody asked the user to install.
    try:
        from skillroute.ui_server import run_ui
    except ImportError as exc:
        raise SystemExit(UI_EXTRA_MISSING) from exc

    run_ui(
        catalog_path=args.catalog,
        host=args.host,
        port=args.port,
        open_browser=not args.no_open,
    )


def run_astra_command(operation: Callable[[], Any]) -> Any:
    try:
        return operation()
    except AstraDataAPIError as exc:
        raise SystemExit(str(exc)) from exc


def parse_options_json(raw: str | None) -> dict[str, Any] | None:
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--options-json is not valid JSON: {exc}") from exc


def count_ref_statuses(refs: list[dict[str, Any]]) -> dict[str, int]:
    statuses: dict[str, int] = {}
    for ref in refs:
        status = ref.get("status", "unknown")
        statuses[status] = statuses.get(status, 0) + 1
    return statuses


def backend_status_payload(catalog: Catalog, backend: RetrievalBackend) -> dict[str, Any]:
    skills = catalog.list_skills()
    status = backend.status(skills)
    ref_summary = catalog.backend_ref_summary(backend.name)
    return {
        "backend": backend.name,
        "configured": bool(status.get("configured")),
        "status": status.get("status", "unknown"),
        "search_available": bool(status.get("search_available")),
        "write_available": bool(status.get("write_available")),
        "catalog": str(catalog.path),
        "skill_count": len(skills),
        "ref_count": ref_summary["ref_count"],
        "ref_status_counts": ref_summary["status_counts"],
        "details": {
            key: value
            for key, value in status.items()
            if key not in {"configured", "status", "search_available", "write_available"}
        },
    }


def print_backend_status(payload: dict[str, Any]) -> None:
    print(f"{payload['backend']} status={payload['status']}")
    print(f"configured: {payload['configured']}")
    print(f"search_available: {payload['search_available']}")
    print(f"write_available: {payload['write_available']}")
    print(f"catalog: {payload['catalog']}")
    print(f"skills: {payload['skill_count']}")
    print(f"backend refs: {payload['ref_count']} {json.dumps(payload['ref_status_counts'], sort_keys=True)}")
    if payload["details"]:
        print(f"details: {json.dumps(payload['details'], sort_keys=True)}")


def print_trace(trace: dict[str, Any]) -> None:
    request = trace["request"]
    response = trace["response"]
    print(f"Trace {trace['id']} {trace['created_at']}")
    print(f"backend: {request.get('backend', 'unknown')}")
    print(f"request: {request.get('request', '')}")
    print(f"clarification_needed: {response.get('clarification_needed')}")
    for index, candidate in enumerate(response.get("candidates", []), start=1):
        score_breakdown = candidate.get("score_breakdown", {})
        print(f"{index}. {candidate.get('name')} confidence={candidate.get('confidence')}")
        print(f"   skill_id: {candidate.get('skill_id')}")
        print(f"   scores: {json.dumps(score_breakdown, sort_keys=True)}")


BRIDGE_LIMIT_MAX = 50


def cmd_bridge(args: argparse.Namespace) -> None:
    payload = json.loads(sys.stdin.read() or "{}")
    catalog = Catalog(payload.get("catalog") or args.catalog or default_catalog_path())
    router = Router(catalog, backend=backend_from_name(payload.get("backend")))
    result: Any
    if args.operation == "route":
        result = to_jsonable(
            router.route(
                require_payload_key(payload, "request"),
                repo=payload.get("repo"),
                limit=clamp_limit(payload.get("limit", 5)),
            )
        )
    elif args.operation == "search":
        result = router.search(
            require_payload_key(payload, "query"), limit=clamp_limit(payload.get("limit", 10))
        )
    else:
        skill_id = require_payload_key(payload, "skill_id")
        skill = catalog.get_skill(skill_id)
        if skill is None:
            raise ValueError(f"Skill not found: {skill_id}")
        result = to_jsonable(skill)
        result["backend_refs"] = catalog.backend_refs(skill.id)
    print_json(result)


def require_payload_key(payload: dict[str, Any], key: str) -> Any:
    if key not in payload:
        raise ValueError(f"Bridge payload is missing required key: {key!r}")
    return payload[key]


def clamp_limit(value: Any) -> int:
    try:
        limit = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Invalid limit: {value!r}") from exc
    return max(1, min(limit, BRIDGE_LIMIT_MAX))


def print_route(response: Any) -> None:
    if response.clarification_needed:
        print("Clarification recommended:")
        for question in response.clarification_questions:
            print(f"- {question}")
    if not response.candidates:
        print("No matching skills.")
        return
    print("Ranked skills:")
    for candidate in response.candidates:
        print(f"{candidate.suggested_position}. {candidate.name} ({candidate.skill_id}) confidence={candidate.confidence}")
        print(f"   {candidate.description}")
        for reason in candidate.reasons[:3]:
            print(f"   reason: {reason}")
        for excerpt in candidate.evidence[:2]:
            print(f"   evidence[{excerpt.kind}]: {excerpt.text}")


def print_json(value: Any) -> None:
    print(json.dumps(to_jsonable(value), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
