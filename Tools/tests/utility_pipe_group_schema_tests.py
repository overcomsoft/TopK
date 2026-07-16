from __future__ import annotations

import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CREATE_SQL = ROOT / "Tools" / "sql" / "create_route_utility_group_vector_tables.sql"
DROP_SQL = ROOT / "Tools" / "sql" / "drop_route_utility_group_vector_tables.sql"
CONTRACT = ROOT / "Tools" / "contracts" / "utility_pipe_group_vector_contract.schema.json"


class UtilityPipeGroupSchemaTests(unittest.TestCase):
    def test_create_migration_is_additive(self):
        text = CREATE_SQL.read_text(encoding="utf-8").upper()
        self.assertIn('CREATE TABLE IF NOT EXISTS "TB_ROUTE_UTILITY_GROUP_VECTOR"', text)
        self.assertIn('CREATE TABLE IF NOT EXISTS "TB_ROUTE_UTILITY_GROUP_MEMBER"', text)
        self.assertIsNone(
            re.search(r"^\s*(TRUNCATE|DELETE\s+FROM|UPDATE|DROP\s+TABLE)\b", text, re.MULTILINE)
        )

    def test_feature_ann_and_context_rerank_contract(self):
        text = CREATE_SQL.read_text(encoding="utf-8").upper()
        self.assertIn('"FEATURE_CENTROID" VECTOR(30) NOT NULL', text)
        self.assertIn('"CONTEXT_CENTROID" VECTOR(30)', text)
        self.assertIn('"FEATURE_CENTROID" VECTOR_COSINE_OPS', text)
        self.assertNotIn('"CONTEXT_CENTROID" VECTOR_COSINE_OPS', text)

    def test_rollback_only_drops_new_tables(self):
        text = DROP_SQL.read_text(encoding="utf-8").upper()
        drop_lines = [line.strip() for line in text.splitlines() if line.strip().startswith("DROP TABLE")]
        self.assertEqual(2, len(drop_lines))
        self.assertTrue(any("TB_ROUTE_UTILITY_GROUP_MEMBER" in line for line in drop_lines))
        self.assertTrue(any("TB_ROUTE_UTILITY_GROUP_VECTOR" in line for line in drop_lines))

    def test_json_contract_has_fixed_30d_vectors(self):
        schema = json.loads(CONTRACT.read_text(encoding="utf-8"))
        feature = schema["properties"]["feature_centroid"]
        context = schema["properties"]["context_centroid"]
        self.assertEqual((30, 30), (feature["minItems"], feature["maxItems"]))
        self.assertEqual((30, 30), (context["minItems"], context["maxItems"]))
        self.assertEqual("utility-pipe-group-v1", schema["properties"]["encoder_version"]["const"])
        self.assertFalse(schema["additionalProperties"])

    def test_schema_inspector_uses_pg_attribute_attname(self):
        migration = (
            ROOT / "Tools" / "MigrateUtilityPipeGroupSchema.py"
        ).read_text(encoding="utf-8")
        self.assertIn("SELECT a.attname, format_type", migration)
        self.assertNotIn("SELECT column_name, format_type", migration)

    def test_schema_inspector_checks_constraints_and_row_counts(self):
        migration = (
            ROOT / "Tools" / "MigrateUtilityPipeGroupSchema.py"
        ).read_text(encoding="utf-8")
        self.assertIn("EXPECTED_CONSTRAINTS", migration)
        self.assertIn('report["row_counts"][table]', migration)


if __name__ == "__main__":
    unittest.main()
