from src.vendor_call_agent import (
    build_call_goal,
    build_call_script,
    build_call_task,
    build_minimal_call_task,
    extract_vendor_specs_from_transcript,
    extract_missing_values_from_transcript,
    get_retell_call_result,
    list_bland_personas,
    make_json_safe,
    get_call_status,
    get_call_transcript,
    explain_provider_failure,
    parse_call_transcript_for_missing_values,
    parse_transcript_to_fields,
    prepare_call_payload,
    start_custom_retell_test_call,
    start_retell_call,
    start_bland_minimal_call,
    start_vendor_call,
    test_bland_connection as bland_connection_check,
)


def test_build_call_script_targets_missing_values():
    row = {"Product Name": "Range", "Brand": "Wolf", "Model/SKU": "GR366"}

    script = build_call_script(row, ["Dimensions"], "555-0100")

    assert "Saffron Case Homes" in script
    assert "Dimensions" in script
    assert "Hello, how are you?" in script
    assert "reference/model number is GR366" in script
    assert "full width, height, and depth" in script
    assert "555-0100" in script


def test_prepare_call_payload_does_not_enable_calling():
    row = {"Product Name": "Range", "Brand": "Wolf"}

    payload = prepare_call_payload(row, ["Dimensions"], "555-0100", custom_goal="Confirm appliance dimensions.")

    assert payload["provider"] is None
    assert "Retell" in payload["future_providers"]
    assert payload["status"] == "Needs Human Review"
    assert payload["call_prepared"] is False
    assert payload["call_completed"] is False
    assert payload["goal"] == "Confirm appliance dimensions."


def test_extract_missing_values_from_transcript_is_review_only_parser():
    transcript = "Dimensions: 36 W x 34 H x 24 D\nBrand: Wolf"

    values = extract_missing_values_from_transcript(transcript, ["Dimensions", "Brand", "Supplier"])

    assert values == {"Dimensions": "36 W x 34 H x 24 D", "Brand": "Wolf"}


def test_build_call_goal_handles_unknown_product():
    goal = build_call_goal({}, ["Supplier"])

    assert "confirm Supplier" in goal
    assert "ask whether they can search by reference or model number" in goal


def test_start_vendor_call_disabled_does_not_call_provider(monkeypatch):
    called = False

    def fake_start_bland(*args, **kwargs):
        nonlocal called
        called = True
        return {"status": "success", "call_id": "call_123"}

    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "false")
    monkeypatch.setenv("BLAND_PROVIDER", "bland")
    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setattr("src.vendor_call_agent.start_bland_call", fake_start_bland)

    result = start_vendor_call({"Product Name": "Range"}, ["Dimensions"], "+12223334444")

    assert result["status"] == "disabled"
    assert called is False


def test_start_vendor_call_missing_api_key(monkeypatch):
    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "true")
    monkeypatch.setenv("VENDOR_CALL_PROVIDER", "bland")
    monkeypatch.setenv("BLAND_PROVIDER", "bland")
    monkeypatch.delenv("BLAND_API_KEY", raising=False)

    result = start_vendor_call({"Product Name": "Range"}, ["Dimensions"], "+12223334444")

    assert result["status"] == "missing_api_key"


def test_start_vendor_call_requires_phone_number(monkeypatch):
    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "true")
    monkeypatch.setenv("BLAND_PROVIDER", "bland")
    monkeypatch.setenv("BLAND_API_KEY", "test-key")

    result = start_vendor_call({"Product Name": "Range"}, ["Dimensions"], "")

    assert result["status"] == "invalid_phone_number"


def test_start_retell_call_sends_only_retell_fields(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 201
        text = '{"call_id":"call_retell","call_status":"registered","agent_id":"agent_123"}'

        def json(self):
            return __import__("json").loads(self.text)

    def fake_post(url, headers, json, timeout=30):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setenv("RETELL_API_KEY", "retell-key")
    monkeypatch.setenv("RETELL_AGENT_ID", "agent_123")
    monkeypatch.setenv("RETELL_PHONE_NUMBER", "+15556667777")
    monkeypatch.setattr("requests.post", fake_post)

    result = start_retell_call(
        {
            "Name of Product": "Refrigerator",
            "Brand": "Sub-Zero",
            "Serial / Model Number": "ID36R",
        },
        ["Dimensions"],
        "+12223334444",
        "Confirm dimensions.",
    )

    assert result["status"] == "call_started"
    assert result["call_id"] == "call_retell"
    assert captured["url"] == "https://api.retellai.com/v2/create-phone-call"
    assert captured["headers"]["Authorization"] == "Bearer retell-key"
    assert captured["body"] == {
        "from_number": "+15556667777",
        "to_number": "+12223334444",
        "override_agent_id": "agent_123",
        "retell_llm_dynamic_variables": {
            "product_name": "Refrigerator",
            "brand": "Sub-Zero",
            "model": "ID36R",
            "missing_field": "Dimensions",
            "call_goal": "Confirm dimensions.",
        },
        "metadata": {
            "source": "sch_designops_intake",
            "provider": "retell",
            "product_name": "Refrigerator",
            "brand": "Sub-Zero",
            "model": "ID36R",
            "missing_fields": "Dimensions",
        },
    }
    assert "persona_id" not in captured["body"]
    assert "voice" not in captured["body"]


def test_start_retell_call_requires_from_number(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "retell-key")
    monkeypatch.setenv("RETELL_AGENT_ID", "agent_123")
    monkeypatch.delenv("RETELL_PHONE_NUMBER", raising=False)

    result = start_retell_call(
        {"Product Name": "Refrigerator", "Brand": "Sub-Zero", "Model/SKU": "ID36R"},
        ["Dimensions"],
        "+12223334444",
        "Confirm dimensions.",
    )

    assert result["status"] == "missing_retell_config"
    assert result["missing_config"] == ["RETELL_PHONE_NUMBER"]
    assert result["friendly_message"] == "Retell outbound calls require a configured Retell phone number. Add RETELL_PHONE_NUMBER to .env/secrets."


def test_start_custom_retell_test_call_uses_custom_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 201
        text = '{"call_id":"call_custom","call_status":"registered","agent_id":"agent_123"}'

        def json(self):
            return __import__("json").loads(self.text)

    def fake_post(url, headers, json, timeout=30):
        captured["url"] = url
        captured["headers"] = headers
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setenv("RETELL_API_KEY", "retell-key")
    monkeypatch.setenv("RETELL_AGENT_ID", "agent_123")
    monkeypatch.setenv("RETELL_PHONE_NUMBER", "+15556667777")
    monkeypatch.setattr("requests.post", fake_post)

    result = start_custom_retell_test_call(
        "+12223334444",
        "Call this vendor and ask for dimensions.",
    )

    assert result["status"] == "call_started"
    assert result["call_id"] == "call_custom"
    assert result["provider"] == "retell"
    assert captured["url"] == "https://api.retellai.com/v2/create-phone-call"
    assert captured["headers"]["Authorization"] == "Bearer retell-key"
    assert captured["body"] == {
        "from_number": "+15556667777",
        "to_number": "+12223334444",
        "agent_id": "agent_123",
        "retell_llm_dynamic_variables": {
            "call_goal": "Call this vendor and ask for dimensions.",
            "product_name": "Custom Test Call",
            "brand": "",
            "model": "",
            "missing_field": "",
        },
        "metadata": {
            "source": "sch_custom_test_call",
            "provider": "retell",
            "call_type": "custom_test",
        },
    }
    assert "override_agent_id" not in captured["body"]
    assert "persona_id" not in captured["body"]
    assert "voice" not in captured["body"]


def test_start_custom_retell_test_call_validates_inputs(monkeypatch):
    monkeypatch.setenv("RETELL_API_KEY", "retell-key")
    monkeypatch.setenv("RETELL_AGENT_ID", "agent_123")
    monkeypatch.setenv("RETELL_PHONE_NUMBER", "+15556667777")

    assert start_custom_retell_test_call("", "Prompt")["status"] == "invalid_request"
    assert start_custom_retell_test_call("12223334444", "Prompt")["message"] == "Phone number must start with +."
    assert start_custom_retell_test_call("+12223334444", "")["message"] == "Call prompt / objective is required."


def test_start_vendor_call_uses_retell_provider(monkeypatch, tmp_path):
    captured = {}

    def fake_retell(row, missing_fields, phone_number, custom_goal=""):
        captured["row"] = row
        captured["missing_fields"] = missing_fields
        captured["phone_number"] = phone_number
        captured["custom_goal"] = custom_goal
        return {
            "status": "call_started",
            "message": "Retell call started.",
            "call_id": "call_retell",
            "provider": "retell",
            "agent_id": "agent_123",
            "debug": {
                "provider": "retell",
                "endpoint": "https://api.retellai.com/v2/create-phone-call",
                "request_body": {"to_number": phone_number, "override_agent_id": "agent_123"},
                "response_status_code": 201,
                "agent_id_used": "agent_123",
                "phone_number_used": phone_number,
                "call_id": "call_retell",
            },
        }

    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "true")
    monkeypatch.setenv("VENDOR_CALL_PROVIDER", "retell")
    monkeypatch.setenv("VENDOR_CALL_MOCK_MODE", "false")
    monkeypatch.setattr("src.vendor_call_agent.CALL_RECORD_DIR", tmp_path)
    monkeypatch.setattr("src.vendor_call_agent.start_retell_call", fake_retell)

    result = start_vendor_call({"Product Name": "Range"}, ["Dimensions"], "+12223334444", "Confirm dimensions.")

    assert result["status"] == "call_started"
    assert result["provider"] == "retell"
    assert result["agent_id"] == "agent_123"
    assert captured["missing_fields"] == ["Dimensions"]


def test_start_vendor_call_blocks_when_retell_config_missing_without_fallback(monkeypatch):
    called_bland = False

    def fake_bland(phone_number, task, metadata):
        nonlocal called_bland
        called_bland = True
        return {"status": "success", "call_id": "call_bland"}

    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "true")
    monkeypatch.setenv("VENDOR_CALL_PROVIDER", "retell")
    monkeypatch.setenv("VENDOR_CALL_MOCK_MODE", "false")
    monkeypatch.delenv("RETELL_API_KEY", raising=False)
    monkeypatch.delenv("RETELL_AGENT_ID", raising=False)
    monkeypatch.delenv("RETELL_PHONE_NUMBER", raising=False)
    monkeypatch.setenv("BLAND_API_KEY", "bland-key")
    monkeypatch.setattr("src.vendor_call_agent.start_bland_call", fake_bland)

    result = start_vendor_call({"Product Name": "Range"}, ["Dimensions"], "+12223334444")

    assert result["status"] == "missing_retell_config"
    assert result["message"] == "Retell config incomplete: missing RETELL_API_KEY, RETELL_AGENT_ID, RETELL_PHONE_NUMBER."
    assert result["missing_config"] == ["RETELL_API_KEY", "RETELL_AGENT_ID", "RETELL_PHONE_NUMBER"]
    assert result["debug"]["missing_config"] == ["RETELL_API_KEY", "RETELL_AGENT_ID", "RETELL_PHONE_NUMBER"]
    assert called_bland is False


def test_start_vendor_call_never_falls_back_from_retell(monkeypatch):
    called_bland = False

    def fake_bland(phone_number, task, metadata):
        nonlocal called_bland
        called_bland = True
        return {"status": "success", "call_id": "call_bland"}

    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "true")
    monkeypatch.setenv("VENDOR_CALL_PROVIDER", "retell")
    monkeypatch.setenv("VENDOR_CALL_MOCK_MODE", "false")
    monkeypatch.delenv("RETELL_API_KEY", raising=False)
    monkeypatch.delenv("RETELL_AGENT_ID", raising=False)
    monkeypatch.delenv("RETELL_PHONE_NUMBER", raising=False)
    monkeypatch.setenv("BLAND_API_KEY", "bland-key")
    monkeypatch.setattr("src.vendor_call_agent.start_bland_call", fake_bland)

    result = start_vendor_call({"Product Name": "Range"}, ["Dimensions"], "+12223334444")

    assert result["status"] == "missing_retell_config"
    assert result["provider"] == "retell"
    assert "RETELL_PHONE_NUMBER" in result["missing_config"]
    assert called_bland is False


def test_build_call_task_includes_required_vendor_behavior():
    row = {"Product Name": "Refrigerator Drawers", "Brand": "Sub-Zero", "Model/SKU": "ID36R"}

    task = build_call_task(row, ["Dimensions"], "")

    assert "Hello, how are you?" in task
    assert "Saffron Case Homes" in task
    assert "reference/model number" in task
    assert "ID36R" in task


def test_build_minimal_call_task_uses_required_format():
    row = {"Brand": "Sub-Zero", "Model/SKU": "SCN60PA1SU"}

    task = build_minimal_call_task(row, ["Dimensions"])

    assert "Hello, how are you?" in task
    assert "Saffron Case Homes" in task
    assert "SCN60PA1SU" in task
    assert "full width, height, and depth" in task


def test_build_call_task_rich_mode_includes_required_vendor_behavior(monkeypatch):
    monkeypatch.setenv("BLAND_MINIMAL_PAYLOAD", "false")
    row = {"Product Name": "Refrigerator Drawers", "Brand": "Sub-Zero", "Model/SKU": "ID36R"}

    task = build_call_task(row, ["Dimensions"], "")

    assert "You are Alley" in task
    assert "Saffron Case Homes" in task
    assert "Sub-Zero" in task
    assert "ID36R" in task
    assert "Hello, how are you?" in task
    assert "wait patiently" in task
    assert "product support email or department" in task


def test_parse_call_transcript_for_missing_values():
    transcript = "Dimensions: 36 W x 34 H x 24 D\nSupplier: Ferguson"

    values = parse_call_transcript_for_missing_values(transcript, ["Dimensions", "Supplier"])

    assert values["Dimensions"] == "36 W x 34 H x 24 D"
    assert values["Supplier"] == "Ferguson"
    assert values["Width"] == "36"
    assert values["Height"] == "34"
    assert values["Depth"] == "24"


def test_extract_vendor_specs_from_transcript_parses_spoken_dimensions():
    transcript = (
        "Hi, yes, for the Scotsman SCN60PA1SU, the dimensions are twenty four inches wide, "
        "twenty three inches deep, and thirty four inches high."
    )

    result = extract_vendor_specs_from_transcript(
        transcript,
        {"Product Name": "Ice Maker", "Brand": "Scotsman", "Model/SKU": "SCN60PA1SU"},
        ["Dimensions"],
    )

    assert result["extracted_fields"]["Dimensions"]["value"] == "24 in W x 23 in D x 34 in H"
    assert result["extracted_fields"]["Dimensions"]["confidence"] == "high"
    assert result["unresolved_fields"] == []


def test_get_retell_call_result_returns_transcript(monkeypatch):
    class FakeResponse:
        ok = True
        status_code = 200
        text = (
            '{"call_id":"call_retell","call_status":"ended",'
            '"transcript_object":[{"role":"agent","content":"Hello"},'
            '{"role":"user","content":"Width is 24, depth is 23, height is 34."}],'
            '"recording_url":"https://example.com/recording.wav",'
            '"call_analysis":{"call_summary":"Vendor confirmed dimensions."}}'
        )

        def json(self):
            return __import__("json").loads(self.text)

    captured = {}

    def fake_get(url, headers, timeout=30):
        captured["url"] = url
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setenv("RETELL_API_KEY", "retell-key")
    monkeypatch.setattr("requests.get", fake_get)

    result = get_retell_call_result("call_retell")

    assert captured["url"] == "https://api.retellai.com/v2/get-call/call_retell"
    assert captured["headers"]["Authorization"] == "Bearer retell-key"
    assert result["status"] == "retrieved"
    assert result["call_status"] == "ended"
    assert "Width is 24" in result["transcript"]
    assert result["recording_url"] == "https://example.com/recording.wav"


def test_start_vendor_call_stores_call_record(monkeypatch, tmp_path):
    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "true")
    monkeypatch.setenv("VENDOR_CALL_PROVIDER", "bland")
    monkeypatch.setenv("VENDOR_CALL_MOCK_MODE", "false")
    monkeypatch.setenv("BLAND_PROVIDER", "bland")
    monkeypatch.setenv("BLAND_AGENT_NAME", "Alley")
    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "+15556667777")
    monkeypatch.setattr("src.vendor_call_agent.CALL_RECORD_DIR", tmp_path)
    monkeypatch.setattr(
        "src.vendor_call_agent.start_bland_call",
        lambda phone_number, task, metadata: {
            "status": "success",
            "message": "Call successfully queued.",
            "call_id": "call_123",
        },
    )

    result = start_vendor_call(
        {"Product Name": "Range", "Brand": "Wolf", "Model/SKU": "GR366"},
        ["Dimensions"],
        "+12223334444",
    )

    assert result["status"] == "call_started"
    assert result["call_id"] == "call_123"
    assert result["agent_name"] == "Alley"
    assert tmp_path.joinpath(result["record_path"].split("/")[-1]).exists()


def test_start_bland_call_uses_configured_from_number(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"status":"success","call_id":"call_123"}'

        def json(self):
            return {"status": "success", "call_id": "call_123"}

    def fake_post(url, headers, json, timeout=30):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "+15556667777")
    monkeypatch.setenv("BLAND_USE_CALLER_NUMBER", "true")
    monkeypatch.setenv("BLAND_PERSONA_ID", "")
    monkeypatch.setenv("BLAND_VOICE", "")
    monkeypatch.setenv("BLAND_VOICE_ID", "")
    monkeypatch.setenv("BLAND_USE_VOICE_OVERRIDE", "false")
    monkeypatch.setenv("BLAND_PATHWAY_ID", "")
    monkeypatch.setenv("BLAND_AGENT_NAME", "")
    monkeypatch.setenv("BLAND_MINIMAL_PAYLOAD", "false")
    monkeypatch.setattr("requests.post", fake_post)

    from src.vendor_call_agent import start_bland_call

    result = start_bland_call("+12223334444", "Call and ask for dimensions.", {})

    assert result["status"] == "call_started"
    assert captured["url"] == "https://us.api.bland.ai/v1/calls"
    assert captured["body"] == {
        "phone_number": "+12223334444",
        "task": "Call and ask for dimensions.",
        "from": "+15556667777",
    }
    assert captured["headers"]["Authorization"] == "test-key"


def test_start_bland_call_resolves_persona_by_agent_name(monkeypatch):
    captured = {}

    class FakeCallResponse:
        ok = True
        status_code = 200
        text = '{"status":"success","call_id":"call_alley"}'

        def json(self):
            return {"status": "success", "call_id": "call_alley"}

    class FakePersonasResponse:
        ok = True
        status_code = 200
        text = '{"data":[{"id":"persona_alley","name":"Alley","role":"Vendor Caller","current_production_version":{"call_config":{"voice":"June"}}}]}'

        def json(self):
            return __import__("json").loads(self.text)

    def fake_post(url, headers, json, timeout=30):
        captured["body"] = json
        return FakeCallResponse()

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_AGENT_NAME", "Alley")
    monkeypatch.setenv("BLAND_PERSONA_ID", "")
    monkeypatch.setenv("BLAND_VOICE", "")
    monkeypatch.setenv("BLAND_VOICE_ID", "")
    monkeypatch.setenv("BLAND_USE_VOICE_OVERRIDE", "true")
    monkeypatch.setenv("BLAND_PATHWAY_ID", "")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "+15556667777")
    monkeypatch.setenv("BLAND_USE_CALLER_NUMBER", "true")
    monkeypatch.setenv("BLAND_MINIMAL_PAYLOAD", "true")
    monkeypatch.setattr("requests.get", lambda url, headers, timeout=20: FakePersonasResponse())
    monkeypatch.setattr("requests.post", fake_post)

    from src.vendor_call_agent import start_bland_call

    result = start_bland_call("+12223334444", "Call and ask for dimensions.", {})

    assert result["status"] == "call_started"
    assert captured["body"] == {
        "phone_number": "+12223334444",
        "task": "Call and ask for dimensions.",
        "from": "+15556667777",
        "voice": "June",
    }
    provider_config = result["debug"]["provider_config"]
    assert provider_config["persona_lookup_status"] == "found"
    assert provider_config["resolved_persona_id"] == "persona_alley"
    assert provider_config["resolved_voice"] == "June"
    assert provider_config["voice_source"] == "persona_lookup"


def test_start_bland_call_tries_voice_then_voice_and_persona_when_voice_only_rejected(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, ok, status_code, text):
            self.ok = ok
            self.status_code = status_code
            self.text = text

        def json(self):
            return __import__("json").loads(self.text)

    class FakePersonasResponse:
        ok = True
        status_code = 200
        text = '{"data":[{"id":"persona_alley","name":"Alley","current_production_version":{"call_config":{"voice":"voice_alley"}}}]}'

        def json(self):
            return __import__("json").loads(self.text)

    def fake_post(url, headers, json, timeout=30):
        calls.append(json)
        if len(calls) == 1:
            return FakeResponse(False, 422, '{"message":"voice not accepted"}')
        return FakeResponse(True, 200, '{"status":"success","call_id":"call_voice_persona"}')

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_AGENT_NAME", "Alley")
    monkeypatch.setenv("BLAND_PERSONA_ID", "")
    monkeypatch.setenv("BLAND_VOICE", "")
    monkeypatch.setenv("BLAND_VOICE_ID", "")
    monkeypatch.setenv("BLAND_USE_VOICE_OVERRIDE", "true")
    monkeypatch.setenv("BLAND_PATHWAY_ID", "")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "+15556667777")
    monkeypatch.setenv("BLAND_USE_CALLER_NUMBER", "true")
    monkeypatch.setattr("requests.get", lambda url, headers, timeout=20: FakePersonasResponse())
    monkeypatch.setattr("requests.post", fake_post)

    from src.vendor_call_agent import start_bland_call

    result = start_bland_call("+12223334444", "Call and ask for dimensions.", {})

    assert result["status"] == "call_started"
    assert result["call_id"] == "call_voice_persona"
    assert calls[0]["voice"] == "voice_alley"
    assert calls[1] == {
        "phone_number": "+12223334444",
        "task": "Call and ask for dimensions.",
        "from": "+15556667777",
        "persona_id": "persona_alley",
        "voice": "voice_alley",
    }
    assert result["debug"]["fallback_used"] is True
    assert result["debug"]["successful_attempt"] == "voice_and_persona"


def test_start_bland_call_minimal_mode_without_config_uses_minimal_payload(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"status":"success","call_id":"call_min"}'

        def json(self):
            return {"status": "success", "call_id": "call_min"}

    def fake_post(url, headers, json, timeout=30):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "")
    monkeypatch.setenv("BLAND_PERSONA_ID", "")
    monkeypatch.setenv("BLAND_VOICE", "")
    monkeypatch.setenv("BLAND_VOICE_ID", "")
    monkeypatch.setenv("BLAND_USE_VOICE_OVERRIDE", "false")
    monkeypatch.setenv("BLAND_PATHWAY_ID", "")
    monkeypatch.setenv("BLAND_MINIMAL_PAYLOAD", "true")
    monkeypatch.setattr("requests.post", fake_post)

    from src.vendor_call_agent import start_bland_call

    result = start_bland_call("+12223334444", "Get the missing Dimensions for Scotsman SCN60PA1SU.", {})
    assert result["status"] == "call_started"
    assert captured["url"] == "https://us.api.bland.ai/v1/calls"
    assert captured["body"] == {
        "phone_number": "+12223334444",
        "task": "Get the missing Dimensions for Scotsman SCN60PA1SU.",
    }
    assert result["debug"]["minimal_payload_fields"] == ["phone_number", "task"]


def test_start_bland_call_minimal_mode_uses_configured_from_number(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"status":"success","call_id":"call_configured"}'

        def json(self):
            return {"status": "success", "call_id": "call_configured"}

    def fake_post(url, headers, json, timeout=30):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "+15556667777")
    monkeypatch.setenv("BLAND_USE_CALLER_NUMBER", "true")
    monkeypatch.setenv("BLAND_MINIMAL_PAYLOAD", "true")
    monkeypatch.setenv("BLAND_PERSONA_ID", "")
    monkeypatch.setenv("BLAND_VOICE", "")
    monkeypatch.setenv("BLAND_VOICE_ID", "")
    monkeypatch.setenv("BLAND_USE_VOICE_OVERRIDE", "false")
    monkeypatch.setenv("BLAND_PATHWAY_ID", "")
    monkeypatch.setenv("BLAND_AGENT_NAME", "")
    monkeypatch.setattr("requests.post", fake_post)

    from src.vendor_call_agent import start_bland_call

    result = start_bland_call("+12223334444", "Call and ask for dimensions.", {})

    assert result["status"] == "call_started"
    assert captured["body"] == {
        "phone_number": "+12223334444",
        "task": "Call and ask for dimensions.",
        "from": "+15556667777",
    }


def test_start_bland_call_does_not_send_from_without_flag(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"status":"success","call_id":"call_default"}'

        def json(self):
            return {"status": "success", "call_id": "call_default"}

    def fake_post(url, headers, json, timeout=30):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "+15556667777")
    monkeypatch.setenv("BLAND_USE_CALLER_NUMBER", "false")
    monkeypatch.setenv("BLAND_MINIMAL_PAYLOAD", "true")
    monkeypatch.setenv("BLAND_PERSONA_ID", "")
    monkeypatch.setenv("BLAND_VOICE", "")
    monkeypatch.setenv("BLAND_VOICE_ID", "")
    monkeypatch.setenv("BLAND_USE_VOICE_OVERRIDE", "false")
    monkeypatch.setenv("BLAND_PATHWAY_ID", "")
    monkeypatch.setenv("BLAND_AGENT_NAME", "")
    monkeypatch.setattr("requests.post", fake_post)

    from src.vendor_call_agent import start_bland_call

    result = start_bland_call("+12223334444", "Call and ask for dimensions.", {})

    assert result["status"] == "call_started"
    assert captured["body"] == {
        "phone_number": "+12223334444",
        "task": "Call and ask for dimensions.",
    }


def test_start_bland_call_uses_pathway_without_task(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"status":"success","call_id":"call_pathway"}'

        def json(self):
            return {"status": "success", "call_id": "call_pathway"}

    def fake_post(url, headers, json, timeout=30):
        captured["body"] = json
        return FakeResponse()

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "")
    monkeypatch.setenv("BLAND_PATHWAY_ID", "pathway_123")
    monkeypatch.setenv("BLAND_PERSONA_ID", "")
    monkeypatch.setenv("BLAND_VOICE", "")
    monkeypatch.setenv("BLAND_VOICE_ID", "")
    monkeypatch.setenv("BLAND_USE_VOICE_OVERRIDE", "false")
    monkeypatch.setenv("BLAND_USE_PATHWAY", "true")
    monkeypatch.setenv("BLAND_MINIMAL_PAYLOAD", "true")
    monkeypatch.setattr("requests.post", fake_post)

    from src.vendor_call_agent import start_bland_call

    result = start_bland_call("+12223334444", "Call and ask for dimensions.", {})

    assert result["status"] == "call_started"
    assert captured["body"] == {
        "phone_number": "+12223334444",
        "pathway_id": "pathway_123",
    }


def test_start_bland_call_falls_back_when_persona_rejected(monkeypatch):
    calls = []

    class FakeResponse:
        def __init__(self, ok, status_code, text, payload):
            self.ok = ok
            self.status_code = status_code
            self.text = text
            self.payload = payload

        def json(self):
            return __import__("json").loads(self.text)

    def fake_post(url, headers, json, timeout=30):
        calls.append(json)
        if len(calls) == 1:
            return FakeResponse(False, 403, '{"message":"persona_id not accepted","error":"1010"}', json)
        return FakeResponse(True, 200, '{"status":"success","call_id":"call_fallback"}', json)

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_MINIMAL_PAYLOAD", "false")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "")
    monkeypatch.setenv("BLAND_PERSONA_ID", "persona_bad")
    monkeypatch.setenv("BLAND_VOICE", "")
    monkeypatch.setenv("BLAND_VOICE_ID", "")
    monkeypatch.setenv("BLAND_USE_VOICE_OVERRIDE", "false")
    monkeypatch.setattr("requests.post", fake_post)

    from src.vendor_call_agent import start_bland_call

    result = start_bland_call("+12223334444", "Call and ask for dimensions.", {})

    assert result["status"] == "call_started"
    assert result["call_id"] == "call_fallback"
    assert result["warning"] == "Bland rejected the Alley voice/persona config, so the call used the default working payload."
    assert calls[0] == {
        "phone_number": "+12223334444",
        "task": "Call and ask for dimensions.",
        "persona_id": "persona_bad",
    }
    assert calls[1] == {
        "phone_number": "+12223334444",
        "task": "Call and ask for dimensions.",
    }


def test_make_json_safe_handles_cycles_and_common_objects(tmp_path):
    data = {"path": tmp_path, "error": ValueError("bad")}
    data["self"] = data

    safe = make_json_safe(data)

    assert safe["path"] == str(tmp_path)
    assert safe["error"] == "bad"
    assert safe["self"] == "[Circular]"


def test_start_bland_minimal_call_uses_only_phone_and_task(monkeypatch):
    captured = {}

    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"status":"success","call_id":"call_min"}'

        def json(self):
            return {"status": "success", "call_id": "call_min"}

    def fake_post(url, headers, json, timeout=30):
        captured["url"] = url
        captured["body"] = json
        captured["headers"] = headers
        return FakeResponse()

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "+15556667777")
    monkeypatch.setenv("BLAND_PERSONA_ID", "persona_123")
    monkeypatch.setattr("requests.post", fake_post)

    result = start_bland_minimal_call("+12223334444", "Call and ask for dimensions.")

    assert result["status"] == "call_started"
    assert result["call_id"] == "call_min"
    assert captured["url"] == "https://us.api.bland.ai/v1/calls"
    assert captured["body"] == {
        "phone_number": "+12223334444",
        "task": "Call and ask for dimensions.",
    }
    assert result["debug"]["headers"]["Authorization"] == "[hidden]"
    assert result["debug"]["response_text"] == '{"status":"success","call_id":"call_min"}'


def test_start_vendor_call_ignores_invalid_bland_phone_number_when_not_sent(monkeypatch):
    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "true")
    monkeypatch.setenv("VENDOR_CALL_PROVIDER", "bland")
    monkeypatch.setenv("VENDOR_CALL_MOCK_MODE", "false")
    monkeypatch.setenv("BLAND_PROVIDER", "bland")
    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setenv("BLAND_PHONE_NUMBER", "555-1234")
    monkeypatch.setattr(
        "src.vendor_call_agent.start_bland_call",
        lambda phone_number, task, metadata: {
            "status": "success",
            "message": "Call successfully queued.",
            "call_id": "call_123",
        },
    )

    result = start_vendor_call({"Product Name": "Range"}, ["Dimensions"], "+12223334444")

    assert result["status"] == "call_started"


def test_explain_provider_failure_maps_common_errors():
    assert "payload or caller number" in explain_provider_failure("error", "error code: 1010")
    assert "BLAND_API_KEY" in explain_provider_failure("error", "401 unauthorized")
    assert "credits" in explain_provider_failure("no_credits", "insufficient balance")
    assert "E.164" in explain_provider_failure("invalid_phone_number", "invalid phone number")


def test_start_vendor_call_mock_mode_does_not_call_provider(monkeypatch, tmp_path):
    called = False

    def fake_start_bland(*args, **kwargs):
        nonlocal called
        called = True
        return {"status": "success", "call_id": "call_123"}

    monkeypatch.setenv("VENDOR_CALLS_ENABLED", "true")
    monkeypatch.setenv("VENDOR_CALL_MOCK_MODE", "true")
    monkeypatch.setenv("BLAND_PROVIDER", "bland")
    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setattr("src.vendor_call_agent.CALL_RECORD_DIR", tmp_path)
    monkeypatch.setattr("src.vendor_call_agent.start_bland_call", fake_start_bland)

    result = start_vendor_call({"Product Name": "Range"}, ["Dimensions"], "+12223334444")

    assert result["status"] == "mock_call_completed"
    assert result["provider"] == "mock"
    assert called is False


def test_test_bland_connection_missing_api_key(monkeypatch):
    monkeypatch.delenv("BLAND_API_KEY", raising=False)

    result = bland_connection_check()

    assert result["status"] == "missing_api_key"
    assert result["account_connection"] == "failed"


def test_list_bland_personas_parses_names(monkeypatch):
    class FakeResponse:
        ok = True
        status_code = 200
        text = '{"data":[{"id":"persona_1","name":"Alley","role":"Vendor Caller","current_production_version":{"call_config":{"voice":"June"}}}]}'

        def json(self):
            return __import__("json").loads(self.text)

    monkeypatch.setenv("BLAND_API_KEY", "test-key")
    monkeypatch.setattr("requests.get", lambda url, headers, timeout=20: FakeResponse())

    result = list_bland_personas()

    assert result["status"] == "connected"
    assert result["personas"][0]["id"] == "persona_1"
    assert result["personas"][0]["name"] == "Alley"
    assert result["personas"][0]["role"] == "Vendor Caller"
    assert result["personas"][0]["voice"] == "June"
    assert result["personas"][0]["call_config"] == {"voice": "June"}
    assert result["matched_persona_id"] == "persona_1"
    assert result["matched_voice"] == "June"
    assert result["env_suggestion"] == "BLAND_PERSONA_ID=persona_1"


def test_parse_transcript_to_fields_extracts_dimension_suggestions():
    transcript = "The width is 36 inches. Height 84. Depth: 24 in."

    values = parse_transcript_to_fields(transcript, ["Dimensions"])

    assert values["Width"] == "36"
    assert values["Height"] == "84"
    assert values["Depth"] == "24"
    assert values["Dimensions"] == '36"W x 84"H x 24"D'


def test_get_call_status_returns_transcript_and_normalized_status(monkeypatch):
    monkeypatch.setenv("VENDOR_CALL_PROVIDER", "bland")
    monkeypatch.setenv("BLAND_PROVIDER", "bland")
    monkeypatch.setattr(
        "src.vendor_call_agent._fetch_bland_call_detail",
        lambda call_id: {
            "call_id": call_id,
            "status": "completed",
            "completed": True,
            "answered_by": "human",
            "concatenated_transcript": "user: Width 36 Height 84 Depth 24",
            "recording_url": "https://example.com/recording.wav",
        },
    )

    result = get_call_status("call_123")

    assert result["status"] == "call_completed"
    assert result["call_id"] == "call_123"
    assert result["recording_url"] == "https://example.com/recording.wav"
    assert "Width 36" in result["transcript"]


def test_get_call_transcript_uses_call_status(monkeypatch):
    monkeypatch.setenv("VENDOR_CALL_PROVIDER", "bland")
    monkeypatch.setenv("BLAND_PROVIDER", "bland")
    monkeypatch.setattr(
        "src.vendor_call_agent._fetch_bland_call_detail",
        lambda call_id: {
            "call_id": call_id,
            "queue_status": "complete",
            "completed": True,
            "transcripts": [
                {"user": "assistant", "text": "Hello"},
                {"user": "user", "text": "Width is 36"},
            ],
        },
    )

    result = get_call_transcript("call_123")

    assert result["status"] == "call_completed"
    assert "assistant: Hello" in result["transcript"]
    assert "user: Width is 36" in result["transcript"]
