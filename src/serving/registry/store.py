"""Model registry.

One invariant, enforced rather than documented: **at most one CHAMPION per model
name.** Promotion is therefore atomic - the outgoing champion is demoted in the same
operation that promotes the incoming one, because a registry that can briefly have two
champions, or none, will eventually do exactly that under load.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..types import ModelVersion, Stage


class RegistryError(RuntimeError):
    pass


@dataclass
class Registry:
    _versions: dict[str, list[ModelVersion]] = field(default_factory=dict)
    # Promotion history, for audit and for rollback.
    history: list[dict] = field(default_factory=list)

    def register(self, name: str, version: int, **kw) -> ModelVersion:
        versions = self._versions.setdefault(name, [])
        if any(v.version == version for v in versions):
            raise RegistryError(f"{name} v{version} already registered")
        mv = ModelVersion(name=name, version=version, **kw)
        versions.append(mv)
        return mv

    def get(self, name: str, version: int) -> ModelVersion:
        for v in self._versions.get(name, []):
            if v.version == version:
                return v
        raise RegistryError(f"no such version: {name} v{version}")

    def versions(self, name: str) -> list[ModelVersion]:
        return sorted(self._versions.get(name, []), key=lambda v: v.version)

    def champion(self, name: str) -> ModelVersion | None:
        return next(
            (v for v in self._versions.get(name, []) if v.stage is Stage.CHAMPION), None
        )

    def challenger(self, name: str) -> ModelVersion | None:
        return next(
            (v for v in self._versions.get(name, []) if v.stage is Stage.CHALLENGER), None
        )

    def shadows(self, name: str) -> list[ModelVersion]:
        return [v for v in self._versions.get(name, []) if v.stage is Stage.SHADOW]

    def promote(self, name: str, version: int, *, reason: str = "") -> ModelVersion:
        """Make a version the champion. Atomic: the old champion is demoted here."""
        incoming = self.get(name, version)
        outgoing = self.champion(name)

        if outgoing is not None and outgoing.version == version:
            return incoming  # already champion; promotion is idempotent

        if outgoing is not None:
            outgoing.stage = Stage.ARCHIVED
            outgoing.traffic = 0.0

        incoming.stage = Stage.CHAMPION
        incoming.traffic = 1.0

        self.history.append({
            "action": "promote", "model": name, "to": version,
            "from": outgoing.version if outgoing else None, "reason": reason,
        })
        return incoming

    def start_canary(self, name: str, version: int, traffic: float = 0.05) -> ModelVersion:
        """Send a slice of traffic to a challenger.

        Refused when there is no champion: a canary is defined relative to the thing
        it is being compared against, and without one this is just an untested model
        taking production traffic.
        """
        if not 0.0 < traffic < 1.0:
            raise RegistryError("canary traffic must be between 0 and 1 exclusive")
        if self.champion(name) is None:
            raise RegistryError(f"{name} has no champion to canary against")

        existing = self.challenger(name)
        if existing is not None and existing.version != version:
            raise RegistryError(
                f"{name} already has challenger v{existing.version}; "
                "one canary at a time or the attribution is meaningless"
            )

        mv = self.get(name, version)
        mv.stage = Stage.CHALLENGER
        mv.traffic = traffic
        self.history.append({"action": "canary", "model": name, "version": version,
                             "traffic": traffic})
        return mv

    def set_canary_traffic(self, name: str, traffic: float) -> ModelVersion:
        challenger = self.challenger(name)
        if challenger is None:
            raise RegistryError(f"{name} has no challenger")
        if not 0.0 < traffic <= 1.0:
            raise RegistryError("traffic must be in (0, 1]")
        challenger.traffic = traffic
        return challenger

    def rollback(self, name: str, *, reason: str = "") -> ModelVersion | None:
        """Abort the canary and give all traffic back to the champion.

        Rollback removes the challenger rather than demoting it to shadow: after a
        failure the question is settled, and leaving it scoring traffic invites
        someone to promote it by mistake.
        """
        challenger = self.challenger(name)
        if challenger is None:
            return None
        challenger.stage = Stage.ARCHIVED
        challenger.traffic = 0.0
        self.history.append({"action": "rollback", "model": name,
                             "version": challenger.version, "reason": reason})
        return challenger

    def add_shadow(self, name: str, version: int) -> ModelVersion:
        mv = self.get(name, version)
        mv.stage = Stage.SHADOW
        mv.traffic = 0.0
        return mv
