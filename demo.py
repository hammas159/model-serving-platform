"""Two canaries that both look broken. Only one of them should be rolled back.

    python demo.py

Scenario A: the challenger is bad, the champion is fine -> roll back.
Scenario B: a shared dependency is down, so both are equally bad -> do not
roll back, because removing the challenger fixes nothing and takes away a
deployment that is no worse than what replaces it.

No network, no models -- the "models" are functions that fail on demand.
"""
import sys

sys.path.insert(0, "src")

from serving.observe.slo import SLO, SLOMonitor
from serving.server import ServingPlatform


def healthy(features):
    return 1


def broken(features):
    raise RuntimeError("upstream feature store timeout")


def build(champion_fn, challenger_fn) -> ServingPlatform:
    p = ServingPlatform(monitor=SLOMonitor(slo=SLO(max_error_rate=0.02, min_samples=50)))
    p.registry.register("risk", 1)
    p.registry.register("risk", 2)
    p.load("risk", 1, champion_fn)
    p.load("risk", 2, challenger_fn)
    p.registry.promote("risk", 1)
    p.registry.start_canary("risk", 2, traffic=0.5)
    return p


SCENARIOS = [
    ("A  challenger broken, champion healthy", healthy, broken),
    ("B  shared upstream down, both broken", broken, broken),
]

print("INPUT")
print("   champion v1 and challenger v2, 50/50 canary split, SLO max_error_rate=2%")
for label, champ, chal in SCENARIOS:
    print(f"   scenario {label}")
print("   2000 requests sent through each")
print()

print("OUTPUT")
for label, champ_fn, chal_fn in SCENARIOS:
    p = build(champ_fn, chal_fn)
    for i in range(2000):
        try:
            p.predict("risk", {}, request_key=f"user-{i}")
        except RuntimeError:
            pass  # a failing model is recorded as an error, not a crash

    champion = p.registry.champion("risk")
    print(f"   scenario {label}")
    print(f"      champion now       v{champion.version if champion else '-'}")
    print(f"      rollbacks fired    {len(p.rollbacks)}")
    for r in p.rollbacks:
        print(f"      reason             {r['reason']}")
    if not p.rollbacks:
        print("      held               both versions breach; rolling back would")
        print("                         remove a deployment that is no better or worse")
    print()

print("   The difference is not in the challenger's numbers. They are equally")
print("   bad in both scenarios. It is in whether the champion is a way out.")
