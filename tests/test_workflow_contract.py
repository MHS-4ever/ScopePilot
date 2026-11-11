import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / "workflows" / "brief-analysis.json"
CANONICAL_SCHEMA_PATH = REPO_ROOT / "schemas" / "analysis-output.schema.json"


def test_39_exported_workflow_json_parses():
    assert WORKFLOW_PATH.exists()
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert data.get("name") == "ScopePilot - Brief Analysis"
    assert "nodes" in data
    assert "connections" in data


def test_40_all_node_connections_reference_existing_nodes():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    node_names = {node["name"] for node in data["nodes"]}

    for source_name, connection_types in data["connections"].items():
        assert (
            source_name in node_names
        ), f"Connection source '{source_name}' does not match any node name"
        for conn_type, target_lists in connection_types.items():
            for target_list in target_lists:
                for target_conn in target_list:
                    target_name = target_conn["node"]
                    assert (
                        target_name in node_names
                    ), f"Connection target '{target_name}' from '{source_name}' does not match any node name"


def test_41_information_extractor_has_gemini_model_connection():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        data = json.load(f)

    nodes_by_name = {node["name"]: node for node in data["nodes"]}
    assert "Extract Requirements" in nodes_by_name
    assert "ScopePilot Gemini Model" in nodes_by_name

    extractor = nodes_by_name["Extract Requirements"]
    assert extractor["type"] == "@n8n/n8n-nodes-langchain.informationExtractor"

    gemini = nodes_by_name["ScopePilot Gemini Model"]
    assert gemini["type"] == "@n8n/n8n-nodes-langchain.lmChatGoogleGemini"

    # Verify connection from Gemini model to extractor
    gemini_connections = data["connections"].get("ScopePilot Gemini Model", {})
    assert "ai_languageModel" in gemini_connections
    connected_targets = [
        target["node"] for target in gemini_connections["ai_languageModel"][0]
    ]
    assert "Extract Requirements" in connected_targets


def test_42_credential_names_are_present_without_secret_values():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        content = f.read()
        data = json.loads(content)

    # Check credential names exist as hints
    assert "ScopePilot Webhook Header Auth" in content
    assert "ScopePilot Backend API Key" in content
    assert "ScopePilot Gemini API" in content

    # Inspect all nodes for accidental secrets
    sensitive_keys = {
        "apiKey",
        "api_key",
        "secret",
        "password",
        "token",
        "private_key",
    }
    for node in data["nodes"]:
        credentials = node.get("credentials", {})
        for cred_type, cred_def in credentials.items():
            for key, val in cred_def.items():
                assert (
                    key not in sensitive_keys
                ), f"Found potential secret key '{key}' in credential definition"
                assert key in {
                    "id",
                    "name",
                }, f"Unexpected credential field '{key}' in node {node['name']}"


def test_43_schema_embedded_in_n8n_matches_canonical_schema():
    with open(CANONICAL_SCHEMA_PATH, "r", encoding="utf-8") as f:
        canonical_schema = json.load(f)

    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow_data = json.load(f)

    nodes_by_name = {node["name"]: node for node in workflow_data["nodes"]}
    extractor_node = nodes_by_name["Extract Requirements"]
    embedded_schema_raw = extractor_node["parameters"]["inputSchema"]
    embedded_schema = json.loads(embedded_schema_raw)

    assert embedded_schema == canonical_schema


def test_44_workflow_contains_no_github_issue_creation_operation():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        content = f.read()
        data = json.loads(content)

    assert "github" not in content.lower()
    for node in data["nodes"]:
        assert "github" not in node["type"].lower()
        assert "issue" not in node.get("name", "").lower()
