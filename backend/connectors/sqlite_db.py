"""SQLite connector using sqlglot for statement classification."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp

from backend.core.models import (
    ActionError,
    ActionRequest,
    ConnectorName,
    Decision,
    ExecResult,
    ExecutionPlan,
    Prepared,
    RuleVerdict,
    strictest,
)
from backend.policy.models import Policy

UNSAFE_TYPES = tuple(
    item
    for item in (
        getattr(exp, "Pragma", None),
        getattr(exp, "Attach", None),
        getattr(exp, "Detach", None),
    )
    if item is not None
)
DESTRUCTIVE_TYPES = tuple(
    item
    for item in (
        getattr(exp, "Drop", None),
        getattr(exp, "Alter", None),
        getattr(exp, "Create", None),
        getattr(exp, "TruncateTable", None),
    )
    if item is not None
)


class SQLiteConnector:
    """Classify, read and write the configured SQLite database."""

    name = "database"

    def __init__(self, policy: Policy, workspace_root: Path, database: Path) -> None:
        self.policy = policy
        self.workspace = Path(workspace_root).resolve()
        self.database = Path(database).resolve()
        try:
            self.relative = self.database.relative_to(self.workspace).as_posix()
        except ValueError as exc:
            raise ActionError("The database must live inside the workspace.") from exc

    async def prepare(self, req: ActionRequest) -> Prepared:
        if req.action == "db_schema":
            return Prepared(
                connector=ConnectorName.DATABASE,
                action="db_schema",
                target="schema",
                display_target="database schema",
                scope="db:schema",
                tier=0,
                read_operation=True,
                meta={"summary": "db_schema"},
            )
        sql = str(req.params.get("sql", req.target or ""))
        if "load_extension" in sql.lower():
            return self._blocked(sql, "UNSAFE_SQL", 5)
        try:
            statements = sqlglot.parse(sql, read="sqlite")
        except Exception:
            return self._blocked(sql, "UNSAFE_SQL", 5)
        statements = [statement for statement in statements if statement is not None]
        if len(statements) != 1:
            return self._blocked(sql, "UNSAFE_SQL", 5)
        tree = statements[0]

        if UNSAFE_TYPES and isinstance(tree, UNSAFE_TYPES):
            return self._blocked(sql, "UNSAFE_SQL", 5)

        tables = [table.name for table in tree.find_all(exp.Table) if table.name]
        tables = list(dict.fromkeys(tables))
        table = tables[0] if tables else "database"

        classification = self._classify(tree)
        if classification is None:
            return self._blocked(sql, "UNSAFE_SQL", 5)
        operation, tier, destructive = classification

        decision = Decision.HOLD if destructive else Decision.ALLOW
        codes = ["DESTRUCTIVE_SQL"] if destructive else []
        protected_columns: list[str] = []
        location_weight = 0.0
        for name in tables:
            columns = self._table_columns(name)
            rule = self.policy.protect_rule_for_table(name, operation, columns)
            if rule is None:
                continue
            protected_columns.extend(rule.columns)
            location_weight = max(location_weight, 0.9 if operation == "read" else 0.0)
            if rule.decision_for(operation) == "BLOCK":
                decision = strictest(decision, Decision.BLOCK)
                codes.append("PROTECTED_TABLE")
                tier = 5
            elif rule.decision_for(operation) == "HOLD":
                decision = strictest(decision, Decision.HOLD)
                codes.append("PROTECTED_TABLE")
                tier = max(tier, 1)
            elif rule.decision_for(operation) == "MASK":
                decision = strictest(decision, Decision.MASK)
                codes.append("PROTECTED_TABLE")
                tier = max(tier, 1)

        protected_columns.extend(self._alias_columns(tree, tables))
        protected_columns = list(dict.fromkeys(protected_columns))
        read_operation = operation == "read"
        rule = RuleVerdict(decision=decision, reason_codes=list(dict.fromkeys(codes)))
        meta: dict[str, Any] = {
            "summary": sql[:300],
            "location_weight": location_weight,
            "protected_columns": protected_columns,
        }
        prepared = Prepared(
            connector=ConnectorName.DATABASE,
            action="db_query",
            target=table,
            display_target=table,
            scope=f"db:{table}",
            tier=tier,
            read_operation=read_operation,
            content=sql if not read_operation else None,
            content_kind="table" if read_operation else "text",
            write_content=sql if not read_operation else None,
            rule=rule,
            needs_snapshot=not read_operation,
            snapshot_kind="database" if not read_operation else None,
            snapshot_target=self.relative,
            meta=meta,
        )
        if decision == Decision.BLOCK:
            prepared.content = None
            return prepared
        if read_operation:
            columns, rows, truncated = self._fetch(sql)
            prepared.meta.update(
                {"columns": columns, "rows": rows, "truncated": truncated}
            )
            prepared.content = json.dumps(
                {"columns": columns, "rows": rows, "truncated": truncated},
                ensure_ascii=False,
                default=str,
            )
        return prepared

    def _blocked(self, sql: str, reason: str, tier: int) -> Prepared:
        return Prepared(
            connector=ConnectorName.DATABASE,
            action="db_query",
            target="database",
            display_target="database",
            scope="db:database",
            tier=tier,
            read_operation=False,
            content=None,
            rule=RuleVerdict(decision=Decision.BLOCK, reason_codes=[reason]),
            meta={"summary": sql[:300]},
        )

    def _classify(self, tree: exp.Expression) -> tuple[str, int, bool] | None:
        if isinstance(tree, exp.Select):
            return "read", 0, False
        if isinstance(tree, exp.Insert):
            return "write", 2, False
        if isinstance(tree, exp.Update):
            has_where = tree.args.get("where") is not None
            return "write", 3 if not has_where else 2, not has_where
        if isinstance(tree, exp.Delete):
            return "delete", 3, True
        if getattr(exp, "Drop", None) and isinstance(tree, exp.Drop):
            return "delete", 3, True
        if getattr(exp, "TruncateTable", None) and isinstance(tree, exp.TruncateTable):
            return "delete", 3, True
        if DESTRUCTIVE_TYPES and isinstance(tree, DESTRUCTIVE_TYPES):
            return "write", 3, True
        if isinstance(tree, exp.With):
            return "read", 0, False
        return None

    def _table_columns(self, table: str) -> list[str]:
        try:
            connection = self._connect_readonly()
        except sqlite3.Error:
            return []
        try:
            rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
        except sqlite3.Error:
            return []
        finally:
            connection.close()
        return [str(row[1]) for row in rows]

    def _alias_columns(self, tree: exp.Expression, tables: list[str]) -> list[str]:
        columns: list[str] = []
        protected: set[str] = set()
        for name in tables:
            protected.update(column.lower() for column in self.policy.protected_columns(name))
        if not protected:
            return columns
        for alias in tree.find_all(exp.Alias):
            source = alias.this
            if isinstance(source, exp.Column) and source.name.lower() in protected:
                alias_name = alias.alias
                if alias_name:
                    columns.append(alias_name)
        return columns

    def _connect_readonly(self) -> sqlite3.Connection:
        uri = self.database.as_uri() + "?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=10)
            connection.execute("PRAGMA query_only=ON")
            return connection
        except sqlite3.Error:
            connection = sqlite3.connect(self.database, timeout=10)
            connection.execute("PRAGMA query_only=ON")
            return connection

    def _fetch(self, sql: str) -> tuple[list[str], list[list[Any]], bool]:
        if not self.database.is_file():
            raise ActionError("The database does not exist.")
        try:
            connection = self._connect_readonly()
        except sqlite3.Error as exc:
            raise ActionError(f"Could not open the database: {exc}") from exc
        try:
            cursor = connection.execute(sql)
            if cursor.description is None:
                return [], [], False
            columns = [str(column[0]) for column in cursor.description]
            fetched = cursor.fetchmany(self.policy.database.max_rows + 1)
            truncated = len(fetched) > self.policy.database.max_rows
            rows = [list(row[: len(columns)]) for row in fetched[: self.policy.database.max_rows]]
            for row in rows:
                for index, value in enumerate(row):
                    if isinstance(value, bytes):
                        row[index] = value.decode("utf-8", errors="replace")
            return columns, rows, truncated
        except sqlite3.Error as exc:
            raise ActionError(f"SQL error: {exc}") from exc
        finally:
            connection.close()

    async def execute(self, prepared: Prepared, plan: ExecutionPlan) -> ExecResult:
        if prepared.action == "db_schema":
            return self._execute_schema()
        if prepared.rule.decision == Decision.BLOCK:
            return ExecResult(ok=True, content=None)
        if prepared.read_operation:
            return ExecResult(
                columns=list(prepared.meta.get("columns", [])),
                rows=list(prepared.meta.get("rows", [])),
                truncated=bool(prepared.meta.get("truncated")),
                meta={"table": prepared.target},
            )
        sql = (
            prepared.meta.get("executed_sql")
            or prepared.write_content
            or prepared.content
            or ""
        )
        try:
            connection = sqlite3.connect(self.database, timeout=10)
            try:
                cursor = connection.execute(sql)
                connection.commit()
                changed = cursor.rowcount
            finally:
                connection.close()
        except sqlite3.Error as exc:
            raise ActionError(f"SQL error: {exc}") from exc
        return ExecResult(
            content=f"Statement executed. Rows changed: {changed}.",
            raw_delta=prepared.content,
            sanitized_delta=sql,
            meta={"rows_changed": changed},
        )

    def _execute_schema(self) -> ExecResult:
        if not self.database.is_file():
            raise ActionError("The database does not exist.")
        connection = sqlite3.connect(self.database, timeout=10)
        try:
            tables = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                ).fetchall()
            ]
            schema: dict[str, list[dict[str, Any]]] = {}
            for table in tables:
                protected = set(self.policy.protected_columns(table))
                columns = []
                for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall():
                    name = str(row[1])
                    columns.append(
                        {
                            "name": name,
                            "type": row[2],
                            "protected": name in protected,
                        }
                    )
                schema[table] = columns
        finally:
            connection.close()
        return ExecResult(
            content=json.dumps({"tables": schema}, ensure_ascii=False),
            content_kind="text",
        )
