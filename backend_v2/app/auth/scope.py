"""
Data scope, enforced in SQL (BLUEPRINT.md §11 "Roles and scopes by state
or district enforced in queries, not only in the UI").

A Scope restricts WHICH works a caller may see, on up to four axes, each
matched against served_work -- the one per-work table that carries all of
them for every work of the served run:
  house                   served_work.house             (LS | RS)
  state                   served_work.state             (Phase 9 location state)
  district_authority_id   served_work.district_authority_id
  mp                      served_work.mp                (current tenure)
An unset axis is unrestricted; all unset = national.

Two ways to apply it, both parameterised (values are bind parameters,
never interpolated):
  direct(alias)  -- conditions on served_work itself
  exists(alias)  -- for any other table keyed by (run_id, work_key)
                    (map_work, case_event via its own run, ...): an EXISTS
                    against served_work, so every table is scoped by the
                    same definition.
Results cached per scope use `key()` in their cache key, so one scope's
figures are never served to another.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Scope:
    house: str | None = None
    state: str | None = None
    district_authority_id: int | None = None
    mp: str | None = None

    @property
    def national(self) -> bool:
        return not (self.house or self.state or self.district_authority_id or self.mp)

    @property
    def geographic(self) -> bool:
        """Narrower than a House: state, district or MP."""
        return bool(self.state or self.district_authority_id or self.mp)

    def key(self) -> tuple:
        return (self.house, self.state, self.district_authority_id, self.mp)

    def _conds(self, a: str) -> tuple[list[str], dict]:
        conds, p = [], {}
        if self.house:
            conds.append(f"{a}house = :scope_house")
            p["scope_house"] = self.house
        if self.state:
            conds.append(f"{a}state = :scope_state")
            p["scope_state"] = self.state
        if self.district_authority_id:
            conds.append(f"{a}district_authority_id = :scope_da")
            p["scope_da"] = self.district_authority_id
        if self.mp:
            conds.append(f"{a}mp = :scope_mp")
            p["scope_mp"] = self.mp
        return conds, p

    def direct(self, alias: str = "") -> tuple[str, dict]:
        """' AND ...' conditions on served_work (alias optional)."""
        conds, p = self._conds(f"{alias}." if alias else "")
        return "".join(f" AND {c}" for c in conds), p

    def exists(self, alias: str, run_col: str = "run_id", key_col: str = "work_key") -> tuple[str, dict]:
        """' AND EXISTS (...)' restricting rows of another (run_id, work_key) table."""
        if self.national:
            return "", {}
        conds, p = self._conds("_sc.")
        sql = (
            f" AND EXISTS (SELECT 1 FROM served_work _sc WHERE _sc.run_id = {alias}.{run_col} "
            f"AND _sc.work_key = {alias}.{key_col} AND {' AND '.join(conds)})"
        )
        return sql, p


NATIONAL = Scope()
