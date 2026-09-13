"""The Streamlit demo the `ui` dependency group declared but never shipped.

Two tabs. The first shows canary traffic actually splitting (and sticky — the same
request key always lands on the same version) with a shadow scoring the same requests
without ever being returned. The second is the distinction this repo exists for:
**a bad canary is rolled back; a shared upstream outage is not**, because rolling back
during an outage removes a healthy deployment and fixes nothing.

Run: streamlit run ui/app.py
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from serving.observe.slo import SLO, SLOMonitor  # noqa: E402
from serving.server import ServingPlatform  # noqa: E402
from serving.types import Stage  # noqa: E402

st.set_page_config(page_title="model-serving-platform demo", layout="wide")
st.title("model-serving-platform")
st.caption(
    "A/B with sticky assignment, canary rollout, shadow traffic, per-version SLOs, "
    "and auto-rollback that knows a bad canary from an upstream outage."
)


def _healthy(value: dict) -> str:
    return "approved"


def _broken(value: dict) -> str:
    raise RuntimeError("model is throwing")


def _build(
    *, canary_traffic: float, challenger_broken: bool, champion_broken: bool, shadow: bool
) -> ServingPlatform:
    platform = ServingPlatform(monitor=SLOMonitor(slo=SLO(min_samples=20)))
    platform.registry.register("risk", 1, stage=Stage.CHAMPION)
    platform.registry.register("risk", 2, stage=Stage.CHALLENGER, traffic=canary_traffic)
    platform.load("risk", 1, _broken if champion_broken else _healthy)
    platform.load("risk", 2, _broken if challenger_broken else _healthy)
    if shadow:
        platform.registry.register("risk", 3, stage=Stage.SHADOW)
        platform.load("risk", 3, _healthy)
    return platform


tab_split, tab_rollback = st.tabs(["Traffic split & shadow", "Auto-rollback"])

with tab_split:
    st.markdown(
        "Assignment is **sticky**: which version a request lands on is a hash of its "
        "key, so the same user does not flip between versions on refresh."
    )
    traffic = st.slider("Canary traffic to v2", 0.0, 1.0, 0.2, 0.05)
    with_shadow = st.checkbox("Run a shadow version (v3)", value=True)
    requests = st.slider("Requests to send", 50, 500, 200, 50)

    if st.button("Send traffic", type="primary"):
        platform = _build(
            canary_traffic=traffic,
            challenger_broken=False,
            champion_broken=False,
            shadow=with_shadow,
        )
        stages = Counter()
        shadow_agreements = 0
        shadow_seen = 0
        for i in range(requests):
            prediction = platform.predict("risk", {"x": i}, request_key=f"user-{i}")
            stages[prediction.stage.value] += 1
            if prediction.shadow:
                shadow_seen += 1
                shadow_agreements += sum(
                    1 for s in prediction.shadow.values() if s["agrees_with_champion"]
                )

        served_by_challenger = stages.get("challenger", 0)
        c1, c2, c3 = st.columns(3)
        c1.metric("Requests", requests)
        c2.metric(
            "Served by v2 (canary)",
            f"{served_by_challenger / requests:.1%}",
            help=f"Requested {traffic:.0%}. Hashing does not land on the target exactly.",
        )
        agreement = f"{shadow_agreements / shadow_seen:.0%}" if shadow_seen else "—"
        c3.metric("Shadow agreement", agreement)
        st.bar_chart(dict(stages))

        st.subheader("Stickiness")
        first = platform.predict("risk", {"x": 1}, request_key="user-7").version
        again = platform.predict("risk", {"x": 999}, request_key="user-7").version
        if first == again:
            st.success(f"`user-7` landed on v{first} both times — assignment is sticky.")
        else:
            st.error(f"`user-7` got v{first} then v{again} — assignment is NOT sticky.")

with tab_rollback:
    st.markdown(
        "The distinction that decides whether rolling back helps at all. Pick which "
        "versions are failing and watch what the platform does."
    )
    scenario = st.radio(
        "Scenario",
        [
            "Bad canary — v2 is broken, v1 is healthy",
            "Upstream outage — both v1 and v2 are broken",
            "Everything healthy",
        ],
    )
    challenger_broken = "Bad canary" in scenario or "Upstream" in scenario
    champion_broken = "Upstream" in scenario

    if st.button("Send 200 requests", type="primary"):
        platform = _build(
            canary_traffic=0.5,
            challenger_broken=challenger_broken,
            champion_broken=champion_broken,
            shadow=False,
        )
        for i in range(200):
            platform.predict("risk", {"x": i}, request_key=f"req-{i}")

        status = platform.status("risk")
        if platform.rollbacks:
            st.warning(
                f"**Rolled back.** {platform.rollbacks[-1]['version']} — "
                f"{platform.rollbacks[-1]['reason']}"
            )
        else:
            st.success("**No rollback.**")

        if "Upstream" in scenario and not platform.rollbacks:
            st.info(
                "Correct: the champion is breaching its SLO too, so this is a shared "
                "upstream problem. Rolling back would remove a deployment that is no "
                "worse than what it would roll back to."
            )
        elif "Bad canary" in scenario and platform.rollbacks:
            st.info(
                "Correct: the challenger breached while the champion stayed healthy, "
                "so the new version is the problem and traffic goes back to v1."
            )

        st.subheader("Status")
        st.json(status)
