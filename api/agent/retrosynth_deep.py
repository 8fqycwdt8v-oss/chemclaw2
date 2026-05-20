"""AiZynthFinder wrapper for the `propose_retrosynthesis_deep` tool.

Tier 3 §H. AiZynthFinder is a heavy dep (~500 MB demo bundle, ~4 GB for
the full USPTO bundle); chemclaw2-backend keeps it behind the
`[retrosynth]` extras. The tool layer imports this module behind a
try/except so deployments without extras pay zero dep tax.

`run_deep_retrosynthesis` is sync because AiZynthFinder's `tree_search`
is sync. The agent tool wraps the call in `asyncio.to_thread` so the
event loop doesn't block — typical run is 30s–5min per target.
"""
from __future__ import annotations

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)


DEFAULT_WALL_SECONDS = 5 * 60


def _build_finder() -> Any:
    """Build an AiZynthFinder instance. Honours `AIZYNTH_CONFIG_PATH` if
    set (operator's custom config pointing at the full USPTO bundle);
    otherwise uses the library's bundled demo config.

    Raises ImportError when the [retrosynth] extras aren't installed —
    caller handles.
    """
    from aizynthfinder.aizynthfinder import AiZynthFinder

    config_path = os.environ.get("AIZYNTH_CONFIG_PATH", "")
    if config_path:
        return AiZynthFinder(configfile=config_path)
    # Default: AiZynthFinder ships a public demo config bundled with
    # the wheel. First call downloads the policy + filter models
    # (~500 MB) into the cache directory.
    return AiZynthFinder()


def run_deep_retrosynthesis(
    target_smiles: str,
    max_routes: int = 5,
) -> dict[str, Any]:
    """Synchronously search for multi-step retrosynthesis routes.

    Returns a dict in the chemclaw2-tool response shape:
        {target, routes: [...], total, model: "aizynthfinder"}
    Each route is a nested dict with the standard AiZynthFinder tree
    shape (smiles, type, children, in_stock, ...).

    Raises ImportError when [retrosynth] extras aren't installed.
    Raises ValueError when target_smiles is empty.
    """
    if not target_smiles or not target_smiles.strip():
        raise ValueError("target_smiles is required")

    finder = _build_finder()  # ImportError handled by caller
    finder.target_smiles = target_smiles
    finder.tree_search()
    finder.build_routes()
    routes = finder.routes

    # AiZynthFinder's routes object is iterable; each route exposes
    # `.reaction_tree.to_dict()` for serialisation. Newer versions also
    # offer `.dict()` directly — try both for forward compat.
    serialised: list[dict[str, Any]] = []
    for route in list(routes)[:max_routes]:
        try:
            tree = route.reaction_tree.to_dict()
        except AttributeError:
            try:
                tree = route.dict()
            except Exception as e:
                logger.warning("aizynth route serialise failed: %s", e)
                continue
        serialised.append(tree)

    return {
        "target": target_smiles,
        "routes": serialised,
        "total": len(serialised),
        "model": "aizynthfinder",
    }
