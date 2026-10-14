import base64
import os
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from google.genai.errors import ClientError, ServerError

from app import (
    GEMINI_TIMEOUT_MS,
    MAX_HISTORY_MESSAGES,
    MAX_HISTORY_TOTAL_LENGTH,
    MAX_MESSAGE_LENGTH,
    MODEL_CANDIDATES,
    app,
    decode_attachments,
    get_client,
    get_api_key,
    generate_reply,
    normalize_history,
)
from assistant_agents import AGENTS, build_system_instruction, route_agent


class AgentRoutingTests(unittest.TestCase):
    def test_routes_common_tasks_to_specialists(self):
        prompts = {
            "Explain photosynthesis for my biology exam.": "Study Agent",
            "Explain acceleration in simple terms.": "Study Agent",
            "Write a Python program to calculate the factorial of 5.": "Coding Agent",
            "Draft a polite email asking for an assignment extension.": "Writing Agent",
            "What is the weather usually like in Iceland?": "General Assistant Agent",
        }
        for prompt, expected_agent in prompts.items():
            with self.subTest(prompt=prompt):
                self.assertEqual(route_agent(prompt).name, expected_agent)

    def test_study_instruction_requests_plain_language_and_delimited_formulas(self):
        instruction = build_system_instruction(AGENTS["study"])

        self.assertIn("define each symbol", instruction)
        self.assertIn("include units", instruction)
        self.assertIn("$...$", instruction)
        self.assertIn("$$...$$", instruction)
        self.assertIn("never put formulas in code blocks", instruction)


class ChatRouteTests(unittest.TestCase):
    def setUp(self):
        app.config.update(TESTING=True)
        self.client = app.test_client()

    def test_api_key_requires_a_configured_value(self):
        for api_key in ("", "your_key_here"):
            with self.subTest(api_key=api_key), patch.dict(os.environ, {"GEMINI_API_KEY": api_key}):
                with self.assertRaisesRegex(ValueError, "Google AI Studio"):
                    get_api_key()

    def test_api_key_is_trimmed_from_environment(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "  configured-key  "}):
            self.assertEqual(get_api_key(), "configured-key")

    def test_client_uses_bounded_timeout_and_disables_sdk_retries(self):
        with patch("app.genai.Client") as client_factory, patch.dict(
            os.environ, {"GEMINI_API_KEY": "configured-key"}
        ):
            get_client()

        options = client_factory.call_args.kwargs["http_options"]
        self.assertEqual(options.timeout, GEMINI_TIMEOUT_MS)
        self.assertEqual(options.retry_options.attempts, 0)

    def test_chat_reports_missing_api_key_without_calling_gemini(self):
        with patch.dict(os.environ, {"GEMINI_API_KEY": ""}):
            response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "configuration_error")
        self.assertNotIn("configured-key", response.get_data(as_text=True))

    def test_home_page_is_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"One assistant. Four specialists.", response.data)
        response.close()

    def test_chat_returns_reply_and_selected_agent(self):
        with patch("app.generate_reply", return_value="A simple explanation.") as generate:
            response = self.client.post(
                "/chat",
                json={"message": "Explain gravity for my physics class.", "history": []},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json(),
            {
                "reply": "A simple explanation.",
                "agent": "Study Agent",
                "request_id": response.get_json()["request_id"],
            },
        )
        self.assertEqual(generate.call_args.args[2].name, "Study Agent")
        self.assertEqual(response.headers["X-Request-ID"], response.get_json()["request_id"])

    def test_chat_routes_photosynthesis_and_factorial_prompts(self):
        prompts = (
            ("Explain photosynthesis.", "Study Agent"),
            ("Write a Python program to calculate the factorial of 5.", "Coding Agent"),
        )
        for prompt, expected_agent in prompts:
            with self.subTest(prompt=prompt), patch(
                "app.generate_reply", return_value="Mocked response."
            ) as generate:
                response = self.client.post("/chat", json={"message": prompt})

            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.get_json()["agent"], expected_agent)
            self.assertEqual(generate.call_args.args[2].name, expected_agent)

    def test_chat_accepts_and_rejects_explicit_agent_names(self):
        with patch("app.generate_reply", return_value="Mocked response.") as generate:
            accepted = self.client.post(
                "/chat",
                json={"message": "Hello", "agent": "Study Agent"},
            )
            rejected = self.client.post(
                "/chat",
                json={"message": "Hello", "agent": "Unknown Agent"},
            )

        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(generate.call_args.args[2].name, "Study Agent")
        self.assertEqual(rejected.status_code, 400)
        self.assertEqual(rejected.get_json()["code"], "invalid_agent")

    def test_generation_uses_selected_agent_and_configured_model(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=[
                        ServerError(
                            503,
                            {"error": {"code": 503, "message": "High demand"}},
                        ),
                        SimpleNamespace(text="Here is the code."),
                    ]
                )
            ),
            close=Mock(),
        )
        agent = route_agent("Write a Python function to add two numbers.")
        with patch("app.get_client", return_value=client):
            reply = generate_reply("Write a Python function to add two numbers.", [], agent)

        self.assertEqual(reply, "Here is the code.")
        request = client.models.generate_content.call_args.kwargs
        self.assertEqual(
            [call.kwargs["model"] for call in client.models.generate_content.call_args_list],
            list(MODEL_CANDIDATES[:2]),
        )
        self.assertIn("programming", request["config"].system_instruction.lower())
        self.assertEqual(request["contents"][-1].role, "user")
        client.close.assert_called_once()

    def test_generation_falls_back_after_gateway_timeout(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=[
                        ServerError(
                            504,
                            {"error": {"code": 504, "message": "Gateway timeout"}},
                        ),
                        SimpleNamespace(text="The fallback model answered."),
                    ]
                )
            ),
            close=Mock(),
        )

        with patch("app.get_client", return_value=client):
            reply = generate_reply("Hello", [], route_agent("Hello"))

        self.assertEqual(reply, "The fallback model answered.")
        self.assertEqual(
            [
                call.kwargs["model"]
                for call in client.models.generate_content.call_args_list
            ],
            list(MODEL_CANDIDATES[:2]),
        )
        client.close.assert_called_once()

    def test_generation_retries_one_transient_network_failure(self):
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=[
                        httpx.ConnectError("connection failed"),
                        SimpleNamespace(text="Recovered after a network retry."),
                    ]
                )
            ),
            close=Mock(),
        )

        with patch("app.get_client", return_value=client), patch("app.time.sleep"):
            reply = generate_reply("Hello", [], route_agent("Hello"))

        self.assertEqual(reply, "Recovered after a network retry.")
        self.assertEqual(client.models.generate_content.call_count, 2)
        client.close.assert_called_once()

    def test_image_attachment_is_passed_to_gemini(self):
        image_data = b"\x89PNG\r\n\x1a\nimage-data"
        attachments = decode_attachments(
            [
                {
                    "name": "diagram.png",
                    "mimeType": "image/png",
                    "data": base64.b64encode(image_data).decode("ascii"),
                }
            ]
        )
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(text="Image described."))
            ),
            close=Mock(),
        )

        with patch("app.get_client", return_value=client):
            reply = generate_reply("Describe this image.", [], None, attachments)

        content = client.models.generate_content.call_args.kwargs["contents"][-1]
        self.assertEqual(reply, "Image described.")
        self.assertEqual(content.parts[0].text, "Describe this image.")
        self.assertEqual(content.parts[1].text, "Attached file: diagram.png")
        self.assertEqual(content.parts[2].inline_data.data, image_data)
        client.close.assert_called_once()

    def test_text_document_content_is_sent_to_gemini(self):
        document_data = b"Notes about acceleration."
        attachments = decode_attachments(
            [
                {
                    "name": "notes.txt",
                    "mimeType": "text/plain",
                    "data": base64.b64encode(document_data).decode("ascii"),
                }
            ]
        )
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(return_value=SimpleNamespace(text="Document summarized."))
            ),
            close=Mock(),
        )

        with patch("app.get_client", return_value=client):
            reply = generate_reply("Summarize this document.", [], None, attachments)

        parts = client.models.generate_content.call_args.kwargs["contents"][-1].parts
        self.assertEqual(reply, "Document summarized.")
        self.assertEqual(parts[1].text, "Attached document (notes.txt):\nNotes about acceleration.")

    def test_chat_validates_and_forwards_image_attachment(self):
        image_data = b"\x89PNG\r\n\x1a\nimage-data"
        with patch("app.generate_reply", return_value="Image described.") as generate:
            response = self.client.post(
                "/chat",
                json={
                    "message": "Describe this image.",
                    "attachments": [
                        {
                            "name": "diagram.png",
                            "mimeType": "image/png",
                            "data": base64.b64encode(image_data).decode("ascii"),
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(generate.call_args.args[3][0]["data"], image_data)

    def test_chat_rejects_attachment_with_mismatched_file_content(self):
        with patch("app.generate_reply") as generate:
            response = self.client.post(
                "/chat",
                json={
                    "message": "Describe this image.",
                    "attachments": [
                        {
                            "name": "diagram.png",
                            "mimeType": "image/png",
                            "data": base64.b64encode(b"not a PNG").decode("ascii"),
                        }
                    ],
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertIn("not a valid PNG", response.get_json()["error"])
        generate.assert_not_called()

    def test_chat_rejects_invalid_message_and_history(self):
        invalid_message = self.client.post("/chat", json={"message": ["not", "text"]})
        empty_message = self.client.post("/chat", json={"message": "  "})
        invalid_history = self.client.post(
            "/chat",
            json={"message": "Hello", "history": {}},
        )
        invalid_history_role = self.client.post(
            "/chat",
            json={"message": "Hello", "history": [{"role": [], "content": "invalid"}]},
        )

        self.assertEqual(invalid_message.status_code, 400)
        self.assertEqual(empty_message.status_code, 400)
        self.assertEqual(empty_message.get_json()["code"], "empty_message")
        self.assertEqual(invalid_history.status_code, 400)
        self.assertEqual(invalid_history_role.status_code, 400)

    def test_chat_rejects_history_over_message_limit(self):
        history = [{"role": "user", "content": f"question {index}"} for index in range(MAX_HISTORY_MESSAGES + 1)]
        response = self.client.post("/chat", json={"message": "Current question", "history": history})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["code"], "history_too_long")

    def test_chat_rejects_oversized_message(self):
        response = self.client.post(
            "/chat",
            json={"message": "x" * (MAX_MESSAGE_LENGTH + 1)},
        )
        self.assertEqual(response.status_code, 400)

    def test_oversized_request_returns_json(self):
        original_limit = app.config["MAX_CONTENT_LENGTH"]
        app.config["MAX_CONTENT_LENGTH"] = 128 * 1024
        try:
            response = self.client.post(
                "/chat",
                data='{"message":"' + ("x" * (129 * 1024)) + '"}',
                content_type="application/json",
            )
        finally:
            app.config["MAX_CONTENT_LENGTH"] = original_limit
        self.assertEqual(response.status_code, 413)
        self.assertIn("too large", response.get_json()["error"])
        self.assertIn("request_id", response.get_json())

    def test_timeout_returns_clear_gateway_timeout(self):
        with patch("app.generate_reply", side_effect=httpx.ConnectTimeout("network timeout")):
            response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 504)
        self.assertIn("timed out", response.get_json()["error"])
        self.assertEqual(response.get_json()["code"], "network_timeout")

    def test_network_connection_failure_returns_safe_json(self):
        with patch("app.generate_reply", side_effect=httpx.ConnectError("private details")):
            response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()["code"], "network_failure")
        self.assertNotIn("private details", response.get_data(as_text=True))

    def test_model_capacity_error_returns_retry_message(self):
        with patch(
            "app.generate_reply",
            side_effect=ServerError(
                503,
                {"error": {"code": 503, "message": "High demand"}},
            ),
        ):
            response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 503)
        self.assertIn("temporarily unavailable", response.get_json()["error"])
        self.assertEqual(response.get_json()["code"], "upstream_unavailable")

    def test_rate_limit_returns_quota_message_without_raw_provider_details(self):
        with patch(
            "app.generate_reply",
            side_effect=ClientError(
                429,
                {"error": {"code": 429, "message": "private provider details"}},
            ),
        ) as generate:
            response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.get_json()["code"], "rate_or_quota_limit")
        self.assertIn("quota limit", response.get_json()["error"])
        self.assertNotIn("private provider details", response.get_data(as_text=True))
        generate.assert_called_once()

    def test_provider_retry_after_is_respected_for_a_bounded_single_retry(self):
        rate_limit = ClientError(
            429,
            {"error": {"code": 429, "message": "rate limited"}},
            httpx.Response(429, headers={"retry-after": "1"}),
        )
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=[rate_limit, SimpleNamespace(text="Retried response.")]
                )
            ),
            close=Mock(),
        )

        with patch("app.get_client", return_value=client), patch("app.time.sleep") as sleep:
            reply = generate_reply("Hello", [], route_agent("Hello"))

        self.assertEqual(reply, "Retried response.")
        self.assertEqual(client.models.generate_content.call_count, 2)
        sleep.assert_called_once_with(1.0)
        client.close.assert_called_once()

    def test_excessive_provider_retry_after_is_not_retried_and_returned_to_client(self):
        rate_limit = ClientError(
            429,
            {"error": {"code": 429, "message": "rate limited"}},
            httpx.Response(429, headers={"retry-after": "60"}),
        )
        with patch("app.generate_reply", side_effect=rate_limit):
            response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "60")
        self.assertEqual(response.get_json()["code"], "rate_or_quota_limit")

    def test_invalid_api_key_error_is_not_confused_with_service_capacity(self):
        with patch(
            "app.generate_reply",
            side_effect=ClientError(
                403,
                {"error": {"code": 403, "message": "Permission denied"}},
            ),
        ):
            response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json()["code"], "ai_permission")

    def test_upstream_status_codes_are_returned_with_safe_messages(self):
        expected = {
            400: ("invalid_upstream_request", 400),
            401: ("ai_authentication", 401),
            403: ("ai_permission", 403),
            404: ("model_not_found", 404),
            429: ("rate_or_quota_limit", 429),
            500: ("upstream_server_error", 500),
            502: ("upstream_gateway_error", 502),
            503: ("upstream_unavailable", 503),
            504: ("upstream_timeout", 504),
        }
        for upstream_status, (code, expected_status) in expected.items():
            error_type = ClientError if upstream_status < 500 else ServerError
            error = error_type(
                upstream_status,
                {"error": {"code": upstream_status, "message": "private provider details"}},
            )
            with self.subTest(status=upstream_status), patch(
                "app.generate_reply", side_effect=error
            ):
                response = self.client.post("/chat", json={"message": "Hello"})

            self.assertEqual(response.status_code, expected_status)
            self.assertEqual(response.get_json()["code"], code)
            self.assertNotIn("private provider details", response.get_data(as_text=True))

    def test_unclassified_gemini_error_includes_http_status(self):
        with patch(
            "app.generate_reply",
            side_effect=ServerError(
                502,
                {"error": {"code": 502, "message": "Bad gateway"}},
            ),
        ):
            response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["code"], "upstream_gateway_error")

    def test_unexpected_errors_return_generic_json_and_safe_traceback_logging(self):
        with self.assertLogs("app", level="ERROR") as captured:
            with patch(
                "app.generate_reply",
                side_effect=RuntimeError("private conversation or key details"),
            ):
                response = self.client.post("/chat", json={"message": "Hello"})

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json()["code"], "internal_error")
        self.assertNotIn("private conversation or key details", response.get_data(as_text=True))
        log_output = "\n".join(captured.output)
        self.assertIn("traceback_frames=", log_output)
        self.assertNotIn("private conversation or key details", log_output)

    def test_history_is_limited_to_recent_entries(self):
        history = [
            message
            for index in range(12)
            for message in (
                {"role": "user", "content": f"question {index}"},
                {"role": "assistant", "content": f"answer {index}"},
            )
        ]
        normalized = normalize_history(history)
        self.assertEqual(len(normalized), MAX_HISTORY_MESSAGES)
        self.assertEqual(normalized[0].role, "user")
        self.assertEqual(normalized[0].parts[0].text, "question 2")
        self.assertEqual(normalized[1].role, "model")
        self.assertEqual(normalized[-1].parts[0].text, "answer 11")

    def test_history_contains_only_complete_turns_and_respects_total_character_limit(self):
        history = []
        for index in range(6):
            history.extend(
                (
                    {"role": "user", "content": f"question {index} " + "a" * 4000},
                    {"role": "assistant", "content": f"answer {index} " + "b" * 4000},
                )
            )
        history.append({"role": "user", "content": "unfinished user request"})

        normalized = normalize_history(history)

        self.assertEqual(len(normalized) % 2, 0)
        self.assertEqual(normalized[0].role, "user")
        self.assertEqual(normalized[-1].role, "model")
        self.assertLessEqual(
            sum(len(part.text or "") for item in normalized for part in item.parts),
            MAX_HISTORY_TOTAL_LENGTH,
        )
        self.assertIn("question 5", normalized[-2].parts[0].text)
        self.assertIn("answer 5", normalized[-1].parts[0].text)

    def test_five_consecutive_chat_turns_keep_context_and_include_current_prompt_once(self):
        prompts = [
            "What is Python?",
            "Give me three examples",
            "Which one is easiest for a beginner?",
            "Show a short example of that",
            "Explain the last line",
        ]
        answers = [
            "Python is a programming language.",
            "Examples include scripting, automation, and web development.",
            "Scripting is usually easiest to start with.",
            "print('Hello, world!')",
            "The print function displays text in the terminal.",
        ]
        client = SimpleNamespace(
            models=SimpleNamespace(
                generate_content=Mock(
                    side_effect=[SimpleNamespace(text=answer) for answer in answers]
                )
            ),
            close=Mock(),
        )
        history = []

        with patch("app.get_client", return_value=client), patch("app.time.sleep"):
            for prompt, answer in zip(prompts, answers):
                response = self.client.post(
                    "/chat",
                    json={"message": prompt, "history": list(history)},
                )
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.get_json()["reply"], answer)
                history.extend(
                    (
                        {"role": "user", "content": prompt},
                        {"role": "assistant", "content": answer},
                    )
                )

        calls = client.models.generate_content.call_args_list
        self.assertEqual(len(calls), len(prompts))
        for index, (call, prompt) in enumerate(zip(calls, prompts)):
            contents = call.kwargs["contents"]
            roles = [item.role for item in contents]
            expected_roles = ["user", "model"] * index + ["user"]
            self.assertEqual(roles, expected_roles)
            submitted_text = [
                part.text
                for item in contents
                for part in item.parts
                if part.text
            ]
            self.assertEqual(submitted_text.count(prompt), 1)
        second_turn_contents = calls[1].kwargs["contents"]
        self.assertEqual(second_turn_contents[1].parts[0].text, answers[0])
        self.assertEqual(second_turn_contents[-1].parts[0].text, prompts[1])
        self.assertEqual(client.close.call_count, len(prompts))


if __name__ == "__main__":
    unittest.main()
