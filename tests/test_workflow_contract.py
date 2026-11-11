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


def test_45_extractor_references_claimed_brief_correctly():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow_data = json.load(f)

    nodes_by_name = {node["name"]: node for node in workflow_data["nodes"]}
    extractor = nodes_by_name["Extract Requirements"]
    text_param = extractor["parameters"]["text"]

    # Verify extractor references Claim Brief Analysis full response structure
    assert "$('Claim Brief Analysis')" in text_param
    assert "body.brief.message" in text_param
    assert "body.brief.project_name" in text_param
    assert "body.brief.original_scope" in text_param
    assert ".first().json.body.brief" in text_param
    assert "JSON.stringify" in text_param


def test_46_cached_requests_route_to_authenticated_read_endpoint():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow_data = json.load(f)

    nodes_by_name = {node["name"]: node for node in workflow_data["nodes"]}
    assert "Get Existing Draft" in nodes_by_name
    get_draft_node = nodes_by_name["Get Existing Draft"]

    # Verify HTTP GET to read endpoint
    assert get_draft_node["type"] == "n8n-nodes-base.httpRequest"
    assert get_draft_node["parameters"]["method"] == "GET"
    assert "/analysis" in get_draft_node["parameters"]["url"]
    assert "$('Claim Brief Analysis').first().json.body.brief.id" in get_draft_node["parameters"]["url"]

    # Verify authenticated with Backend API Key credential
    creds = get_draft_node["credentials"]["httpHeaderAuth"]
    assert creds["name"] == "ScopePilot Backend API Key"

    # Verify connection from Check If Completed -> Get Existing Draft -> Return Existing Result
    completed_conns = workflow_data["connections"]["Check If Completed"]["main"]
    assert completed_conns[0][0]["node"] == "Get Existing Draft"

    get_draft_conns = workflow_data["connections"]["Get Existing Draft"]["main"]
    assert get_draft_conns[0][0]["node"] == "Return Existing Result"


def test_47_cached_requests_do_not_invoke_gemini():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow_data = json.load(f)

    # Trace all nodes reachable from Check If Completed branch 0
    visited = set()
    queue = ["Get Existing Draft"]
    while queue:
        current = queue.pop(0)
        visited.add(current)
        for target_list in workflow_data["connections"].get(current, {}).get("main", []):
            for conn in target_list:
                node_name = conn["node"]
                if node_name not in visited:
                    queue.append(node_name)

    assert "Extract Requirements" not in visited
    assert "ScopePilot Gemini Model" not in visited


def test_48_cached_and_newly_completed_responses_have_consistent_structures():
    from scopepilot.analysis_schemas import AnalysisReadResponse

    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow_data = json.load(f)

    nodes_by_name = {node["name"]: node for node in workflow_data["nodes"]}
    existing_resp_node = nodes_by_name["Return Existing Result"]
    new_resp_node = nodes_by_name["Return Analysis Result"]

    # Both return responseBody pointing to the upstream response body
    assert "$json.body" in existing_resp_node["parameters"]["responseBody"]
    assert "$json.body" in new_resp_node["parameters"]["responseBody"]

    # Both endpoints (GET /analysis and POST /analysis/complete) produce AnalysisReadResponse
    read_fields = set(AnalysisReadResponse.model_fields.keys())
    expected_fields = {
        "analysis_id",
        "brief_id",
        "status",
        "attempt_count",
        "model_id",
        "prompt_version",
        "schema_version",
        "completed_at",
        "result",
        "last_error_code",
    }
    assert read_fields == expected_fields


def test_49_model_id_metadata_matches_configured_gemini_model():
    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow_data = json.load(f)

    nodes_by_name = {node["name"]: node for node in workflow_data["nodes"]}
    gemini_node = nodes_by_name["ScopePilot Gemini Model"]
    save_node = nodes_by_name["Validate and Save Analysis"]

    gemini_model = gemini_node["parameters"]["modelName"]
    save_body = save_node["parameters"]["body"]

    # Ensure model_id recorded matches the Gemini modelName parameter exactly
    assert f"model_id: '{gemini_model}'" in save_body or f'model_id: "{gemini_model}"' in save_body
    assert gemini_model == "gemini-3.5-flash-lite"


def test_50_prompt_embedded_in_n8n_matches_canonical_prompt():
    prompt_path = REPO_ROOT / "prompts" / "brief-extraction-v1.md"
    with open(prompt_path, "r", encoding="utf-8") as f:
        canonical_prompt = f.read()

    with open(WORKFLOW_PATH, "r", encoding="utf-8") as f:
        workflow_data = json.load(f)

    nodes_by_name = {node["name"]: node for node in workflow_data["nodes"]}
    extractor = nodes_by_name["Extract Requirements"]
    embedded_prompt = extractor["parameters"]["options"]["systemPromptTemplate"]

    assert embedded_prompt == canonical_prompt
