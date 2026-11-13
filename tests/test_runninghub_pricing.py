# -*- coding: utf-8 -*-
"""RunningHub 事前估价体系测试：纯逻辑层 + main.py 收口链路，全部离线，不触网。"""

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import main
import runninghub_pricing as rp


class EstimatePriorityTests(unittest.TestCase):
    """三档闸门：实测历史 > 单价表/手工价 > GPU 分钟兜底 > unknown。"""

    def test_no_evidence_returns_unknown(self):
        result = rp.estimate_price(rp.empty_price_table(), None, "model", "rhart-image-g-2/text-to-image")
        self.assertEqual(result["confidence"], "unknown")
        self.assertIsNone(result["amount"])

    def test_invalid_kind_or_id_returns_unknown(self):
        self.assertEqual(rp.estimate_price({}, None, "bogus", "x")["confidence"], "unknown")
        self.assertEqual(rp.estimate_price({}, None, "model", "")["confidence"], "unknown")

    def test_dense_history_beats_table(self):
        table = {"models": {"ep": {"billing": "per_unit", "unit_cny": 9.99}}}
        stats = {"samples": [0.17, 0.21, 0.16, 0.19], "count": 4}
        result = rp.estimate_price(table, stats, "model", "ep")
        self.assertEqual(result["source"], "history")
        self.assertEqual(result["confidence"], "high")
        self.assertEqual(result["amount"], 0.18)  # 中位数 (0.17+0.19)/2
        self.assertEqual(result["samples"], 4)

    def test_sparse_history_gives_range_and_medium_confidence(self):
        result = rp.estimate_price({}, {"samples": [0.30, 0.50], "count": 2}, "model", "ep")
        self.assertEqual(result["source"], "history_sparse")
        self.assertEqual(result["confidence"], "medium")
        self.assertEqual(result["low"], 0.30)
        self.assertEqual(result["high"], 0.50)

    def test_zero_only_samples_do_not_count_as_history(self):
        # 失败任务实扣为 0/None，绝不能污染实测路线。
        result = rp.estimate_price(
            {"models": {"ep": {"billing": "per_unit", "unit_cny": 0.2}}},
            {"samples": [0, None], "count": 2},
            "model", "ep",
        )
        self.assertEqual(result["source"], "table")

    def test_table_multiplies_unit_price_by_quantity(self):
        table = {"models": {"ep": {"billing": "per_unit", "unit_cny": 0.2, "unit_quantity_param": "n", "default_quantity": 1}}}
        result = rp.estimate_price(table, None, "model", "ep", quantity=3)
        self.assertEqual(result["source"], "table")
        self.assertEqual(result["amount"], 0.6)

    def test_table_quantity_falls_back_to_default(self):
        table = {"models": {"ep": {"billing": "per_second", "unit_cny": 0.5, "default_quantity": 5}}}
        result = rp.estimate_price(table, None, "model", "ep", quantity=None)
        self.assertEqual(result["amount"], 2.5)

    def test_fixed_billing_ignores_quantity(self):
        table = {"models": {"ep": {"billing": "fixed", "unit_cny": 0.42}}}
        result = rp.estimate_price(table, None, "model", "ep", quantity=8)
        self.assertEqual(result["amount"], 0.42)

    def test_app_manual_price(self):
        table = {"apps": {"1877265245566922800": {"manual_cny": 0.35}}}
        result = rp.estimate_price(table, None, "app", "1877265245566922800")
        self.assertEqual(result["source"], "manual")
        self.assertEqual(result["amount"], 0.35)
        self.assertEqual(result["confidence"], "medium")

    def test_gpu_minute_fallback_only_for_apps_and_workflows(self):
        table = {"fallback": {"gpu_minute_cny": 0.28}}
        app_result = rp.estimate_price(table, None, "app", "123")
        self.assertEqual(app_result["source"], "fallback")
        self.assertEqual(app_result["confidence"], "low")
        model_result = rp.estimate_price(table, None, "model", "ep")
        self.assertEqual(model_result["confidence"], "unknown")

    def test_history_isolates_by_profile_key(self):
        key_a = rp.stats_key("Profile-A", "model", "ep")
        key_b = rp.stats_key("profile-b", "model", "ep")
        self.assertNotEqual(key_a, key_b)
        self.assertEqual(key_a, "profile-a::model::ep")


class ObservedRecallTests(unittest.TestCase):
    def test_apply_observed_appends_and_recomputes(self):
        entry = rp.apply_observed(None, 0.17, 1000.0)
        self.assertEqual(entry["samples"], [0.17])
        self.assertEqual(entry["median"], 0.17)
        entry = rp.apply_observed(entry, 0.21, 2000.0)
        self.assertEqual(entry["count"], 2)
        self.assertEqual(entry["p25"], 0.17)
        self.assertEqual(entry["p75"], 0.21)

    def test_apply_observed_skips_invalid_amounts(self):
        self.assertIsNone(rp.apply_observed(None, 0, 1.0))
        self.assertIsNone(rp.apply_observed(None, None, 1.0))
        self.assertIsNone(rp.apply_observed(None, "abc", 1.0))
        self.assertIsNone(rp.apply_observed(None, -1, 1.0))

    def test_apply_observed_caps_samples_fifo(self):
        entry = None
        for i in range(rp.HISTORY_SAMPLE_CAP + 10):
            entry = rp.apply_observed(entry, 0.1 + (i % 5) * 0.01, float(i))
        self.assertEqual(len(entry["samples"]), rp.HISTORY_SAMPLE_CAP)
        # 只保留最近 HISTORY_SAMPLE_CAP 条：最后写入的 i=CAP+9 仍在，最早的已被挤掉。
        self.assertEqual(entry["samples"][-1], 0.1 + ((rp.HISTORY_SAMPLE_CAP + 9) % 5) * 0.01)
        self.assertEqual(entry["count"], rp.HISTORY_SAMPLE_CAP)

    def test_apply_observed_does_not_mutate_input(self):
        original = {"samples": [0.2], "count": 1}
        rp.apply_observed(original, 0.3, 5.0)
        self.assertEqual(original["samples"], [0.2])

    def test_extract_consume_money_prefers_consume_then_third_party(self):
        self.assertEqual(rp.extract_consume_money({"consumeMoney": 0.17}), 0.17)
        self.assertEqual(rp.extract_consume_money({"consumeMoney": None, "thirdPartyConsumeMoney": 0.5}), 0.5)
        self.assertIsNone(rp.extract_consume_money({"consumeMoney": None, "thirdPartyConsumeMoney": None}))
        self.assertIsNone(rp.extract_consume_money({"consumeMoney": 0}))
        self.assertIsNone(rp.extract_consume_money(None))


class PriceTableValidationTests(unittest.TestCase):
    def test_normalize_accepts_valid_and_drops_noise(self):
        table, errors = rp.normalize_price_table({
            "models": {"ep": {"billing": "per_unit", "unit_cny": 0.2, "junk": "x"}},
            "apps": {"123": {"manual_cny": 0.3}},
            "workflows": {},
            "fallback": {"gpu_minute_cny": 0.28},
            "unexpected": "drop me",
        })
        self.assertEqual(errors, [])
        self.assertEqual(table["models"]["ep"]["unit_cny"], 0.2)
        self.assertNotIn("junk", table["models"]["ep"])
        self.assertNotIn("unexpected", table)
        self.assertEqual(table["schema_version"], rp.PRICING_SCHEMA_VERSION)

    def test_normalize_rejects_bad_values(self):
        _table, errors = rp.normalize_price_table({
            "models": {
                "bad-billing": {"billing": "per-parcel", "unit_cny": 0.2},
                "bad-price": {"billing": "per_unit", "unit_cny": -1},
                "huge-price": {"billing": "per_unit", "unit_cny": 99999},
            },
        })
        self.assertEqual(len(errors), 3)

    def test_normalize_garbage_input_yields_empty_table(self):
        table, errors = rp.normalize_price_table("not a dict")
        self.assertEqual(errors, [])
        self.assertEqual(table["models"], {})


class MainPricingIntegrationTests(unittest.TestCase):
    """main.py 侧的持久化、回流与路由收口（临时目录隔离，不碰真实 data/）。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.table_path = str(Path(self.temp_dir.name) / "runninghub_prices.json")
        self.stats_path = str(Path(self.temp_dir.name) / "runninghub_price_stats.json")
        self.official_path = str(Path(self.temp_dir.name) / "official_prices.json")
        self.patchers = [
            patch.object(main, "RUNNINGHUB_PRICE_TABLE_FILE", self.table_path),
            patch.object(main, "RUNNINGHUB_PRICE_STATS_FILE", self.stats_path),
            patch.object(main, "RUNNINGHUB_OFFICIAL_PRICES_FILE", self.official_path),
        ]
        for patcher in self.patchers:
            patcher.start()
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def test_missing_table_reads_as_empty_without_writing(self):
        table = main.load_runninghub_price_table()
        self.assertEqual(table["schema_version"], rp.PRICING_SCHEMA_VERSION)
        self.assertFalse(Path(self.table_path).exists())

    def test_corrupt_stats_rebuilds_in_memory(self):
        Path(self.stats_path).write_text("{broken", encoding="utf-8")
        data = main.load_runninghub_price_stats()
        self.assertEqual(data["entries"], {})

    def test_save_table_roundtrip_and_rejects_invalid(self):
        saved = main.save_runninghub_price_table({"models": {"ep": {"billing": "per_unit", "unit_cny": 0.2}}})
        self.assertEqual(saved["models"]["ep"]["unit_cny"], 0.2)
        loaded = main.load_runninghub_price_table()
        self.assertEqual(loaded["models"]["ep"]["billing"], "per_unit")
        with self.assertRaises(main.HTTPException) as ctx:
            main.save_runninghub_price_table({"models": {"ep": {"billing": "per_unit", "unit_cny": -5}}})
        self.assertEqual(ctx.exception.status_code, 400)

    def test_record_observed_persists_and_feeds_estimate(self):
        main.record_runninghub_observed_cost("legacy-shared", "model", "ep", 0.17)
        main.record_runninghub_observed_cost("legacy-shared", "model", "ep", 0.21)
        main.record_runninghub_observed_cost("legacy-shared", "model", "ep", 0.19)
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        estimate = main.runninghub_estimate_for_profile("legacy-shared", "model", "ep")
        self.assertEqual(estimate["source"], "history")
        self.assertEqual(estimate["amount"], 0.19)
        # 别的配置组不能共享样本。
        other = main.runninghub_estimate_for_profile("other-group", "model", "ep")
        self.assertEqual(other["confidence"], "unknown")

    def test_record_observed_ignores_zero_cost(self):
        main.record_runninghub_observed_cost("p1", "model", "ep", 0)
        main.record_runninghub_observed_cost("p1", "model", "ep", None)
        data = main.load_runninghub_price_stats()
        self.assertEqual(data["entries"], {})

    def test_recall_from_query_response_nested_usage(self):
        scope = {
            "api_profile_id": "p2",
            "metadata": {"rh_price_kind": "app", "rh_price_id": "9001"},
        }
        response = {"code": 0, "data": {"status": "SUCCESS", "usage": {"consumeMoney": 0.42, "taskCostTime": "12"}}}
        main.runninghub_recall_observed_cost(scope, response)
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        estimate = main.runninghub_estimate_for_profile("p2", "app", "9001")
        self.assertEqual(estimate["source"], "history_sparse")
        self.assertEqual(estimate["amount"], 0.42)

    def test_recall_tolerates_missing_metadata_and_bad_payload(self):
        main.runninghub_recall_observed_cost({}, None)
        main.runninghub_recall_observed_cost({"metadata": {}}, {"code": 0})
        main.runninghub_recall_observed_cost(
            {"metadata": {"rh_price_kind": "app", "rh_price_id": "1"}, "api_profile_id": "p"},
            {"data": {"usage": {"consumeMoney": None}}},
        )

    def test_event_cost_recall_only_for_runninghub_models(self):
        event = {
            "provider": "runninghub",
            "model": "gpt-image-2.0/text-to-image-channel-low-price",
            "api_profile_id": "p3",
        }
        main.runninghub_record_event_cost(event, {"consumeMoney": 0.16})
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        estimate = main.runninghub_estimate_for_profile("p3", "model", "rhart-image-g-2/text-to-image")
        self.assertEqual(estimate["amount"], 0.16)

        # 非 RunningHub / 无模型 / app 事件都不走这条路线。
        main.runninghub_record_event_cost({**event, "provider": "openai"}, {"consumeMoney": 1})
        main.runninghub_record_event_cost({**event, "model": ""}, {"consumeMoney": 1})
        main.runninghub_record_event_cost({**event, "model": "app:123"}, {"consumeMoney": 1})
        main.runninghub_record_event_cost(event, {"consumeMoney": None})

    def test_finish_usage_event_success_records_cost(self):
        captured = []
        event = {
            "id": "evt-1", "provider": "runninghub", "model": "some-model",
            "api_profile_id": "p4", "status": "queued",
        }
        with patch.object(main, "usage_events", return_value=[]), \
             patch.object(main, "append_usage_event"), \
             patch.object(main, "record_runninghub_observed_cost",
                          side_effect=lambda *args: captured.append(args)):
            main.finish_usage_event(event, "succeeded", raw_usage={"consumeMoney": 0.25, "taskCostTime": "9"})
        self.assertEqual(captured, [("p4", "model", "some-model", 0.25)])

        # 失败收口不记录费用。
        captured.clear()
        with patch.object(main, "usage_events", return_value=[]), \
             patch.object(main, "append_usage_event"), \
             patch.object(main, "record_runninghub_observed_cost",
                          side_effect=lambda *args: captured.append(args)):
            main.finish_usage_event(event, "failed", error="boom", raw_usage={"consumeMoney": 0.25})
        self.assertEqual(captured, [])

    def test_usage_from_response_covers_both_shapes(self):
        self.assertEqual(main.usage_from_response({"usage": {"consumeMoney": 1}}), {"consumeMoney": 1})
        self.assertEqual(main.usage_from_response({"data": {"usage": {"consumeMoney": 2}}}), {"consumeMoney": 2})
        self.assertIsNone(main.usage_from_response({"data": {}}))
        self.assertIsNone(main.usage_from_response(None))
        # 传入本身就是 usage 对象也能识别。
        self.assertEqual(main.usage_from_response({"consumeMoney": 3}), {"consumeMoney": 3})

    def test_seed_price_makes_layer_decompose_priced_immediately(self):
        # 本项目实测过图层拆分实扣，种子条目保证这条运行按钮首日就有数字。
        estimate = main.runninghub_estimate_for_profile(
            "legacy-shared", "model", "seedream-v5.5/layer-decomposition"
        )
        self.assertEqual(estimate["source"], "table")
        self.assertEqual(estimate["amount"], 0.19)

    def test_admin_table_overrides_seed(self):
        main.save_runninghub_price_table({
            "models": {"seedream-v5.5/layer-decomposition": {"billing": "fixed", "unit_cny": 0.25}},
        })
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        estimate = main.runninghub_estimate_for_profile(
            "legacy-shared", "model", "seedream-v5.5/layer-decomposition"
        )
        self.assertEqual(estimate["amount"], 0.25)

    def test_seed_is_not_written_into_admin_file(self):
        main.load_runninghub_price_table()
        self.assertFalse(Path(self.table_path).exists())

    def test_model_display_name_normalizes_to_endpoint(self):
        self.assertEqual(
            main.runninghub_model_entry_id("gpt-image-2.0/text-to-image-channel-low-price"),
            "rhart-image-g-2/text-to-image",
        )
        self.assertEqual(
            main.runninghub_model_entry_id("seedream-v5.5-layer-decomposition"),
            "seedream-v5.5/layer-decomposition",
        )
        self.assertEqual(main.runninghub_model_entry_id("app:123"), "")


class EstimateRouteTests(unittest.IsolatedAsyncioTestCase):
    """GET /api/runninghub/estimate 的鉴权与参数行为。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.patchers = [
            patch.object(main, "RUNNINGHUB_PRICE_TABLE_FILE", str(Path(self.temp_dir.name) / "prices.json")),
            patch.object(main, "RUNNINGHUB_PRICE_STATS_FILE", str(Path(self.temp_dir.name) / "stats.json")),
            patch.object(main, "RUNNINGHUB_OFFICIAL_PRICES_FILE", str(Path(self.temp_dir.name) / "official.json")),
        ]
        for patcher in self.patchers:
            patcher.start()
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    async def test_route_returns_estimate_for_current_profile(self):
        profile = {"id": "Legacy-Shared", "enabled": True}
        with patch.object(main, "request_api_profile", return_value=profile):
            result = await main.runninghub_estimate(
                request=None, kind="model", id="rhart-image-g-2/text-to-image", quantity=""
            )
        self.assertTrue(result["success"])
        self.assertEqual(result["estimate"]["confidence"], "unknown")

    async def test_route_rejects_unknown_kind(self):
        with patch.object(main, "request_api_profile", return_value={"id": "p"}):
            result = await main.runninghub_estimate(request=None, kind="bogus", id="x", quantity="")
        self.assertEqual(result["estimate"]["confidence"], "unknown")

    async def test_route_passes_model_id_through_for_key_expansion(self):
        # 路由不再提前把产品名换成端点名：候选键展开（产品名 → 官网 SKU 名 → 端点）
        # 统一由 runninghub_price_lookup_keys 负责，否则渠道版价格会被别名吃掉档位。
        with patch.object(main, "request_api_profile", return_value={"id": "p"}), \
             patch.object(main, "runninghub_estimate_for_profile",
                          return_value={"confidence": "high", "amount": 0.2}) as estimate:
            await main.runninghub_estimate(
                request=None, kind="model", id="gpt-image-2.0/text-to-image-channel-low-price", quantity="", res="2k"
            )
        estimate.assert_called_once_with(
            "p", "model", "gpt-image-2.0/text-to-image-channel-low-price", None, res="2k"
        )

    async def test_unassigned_profile_gets_403(self):
        request = object()
        with patch.object(main, "request_api_profile",
                          side_effect=main.HTTPException(status_code=403, detail="未分配")):
            with self.assertRaises(main.HTTPException) as ctx:
                await main.runninghub_estimate(request=request, kind="model", id="ep", quantity="")
        self.assertEqual(ctx.exception.status_code, 403)


class OfficialPriceTests(unittest.TestCase):
    """官方标价（static/runninghub/official_prices.json）并入估价视图的行为。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.table_path = str(Path(self.temp_dir.name) / "prices.json")
        self.stats_path = str(Path(self.temp_dir.name) / "stats.json")
        self.official_path = str(Path(self.temp_dir.name) / "official.json")
        self.patchers = [
            patch.object(main, "RUNNINGHUB_PRICE_TABLE_FILE", self.table_path),
            patch.object(main, "RUNNINGHUB_PRICE_STATS_FILE", self.stats_path),
            patch.object(main, "RUNNINGHUB_OFFICIAL_PRICES_FILE", self.official_path),
        ]
        for patcher in self.patchers:
            patcher.start()
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def _write_official(self, models):
        Path(self.official_path).write_text(
            json.dumps({"schema_version": 1, "models": models}, ensure_ascii=False),
            encoding="utf-8",
        )
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def test_normalize_keeps_tier_range_and_drops_invalid(self):
        cleaned = rp.normalize_official_prices({
            "models": {
                "ep/tiered": {
                    "billing": "per_unit",
                    "unit_cny": 0.13,
                    "low_cny": 0.13,
                    "high_cny": 0.52,
                    "price_text": "¥0.13~0.52/张",
                    "unit_label": "张",
                },
                "ep/tokens": {"billing": "per_unit", "unit_cny": 0.063, "unit_label": "千tokens"},
                "ep/bad-billing": {"billing": "per_hour", "unit_cny": 1},
                "ep/bad-unit": {"billing": "per_unit", "unit_cny": 0},
            }
        })
        self.assertEqual(set(cleaned), {"ep/tiered", "ep/tokens"})
        self.assertEqual(cleaned["ep/tiered"]["low_cny"], 0.13)
        self.assertEqual(cleaned["ep/tiered"]["high_cny"], 0.52)
        self.assertEqual(cleaned["ep/tiered"]["source"], "official")
        self.assertEqual(cleaned["ep/tiered"]["confidence"], "high")
        # 按 token 计价的单价依赖实际用量，只能给中等置信度。
        self.assertEqual(cleaned["ep/tokens"]["confidence"], "medium")

    def test_official_price_surfaces_as_range_without_history(self):
        self._write_official({"ep/tiered": {
            "billing": "per_unit", "unit_cny": 0.13, "low_cny": 0.13, "high_cny": 0.52,
            "price_text": "¥0.13~0.52/张", "unit_label": "张",
        }})
        estimate = main.runninghub_estimate_for_profile("legacy-shared", "model", "ep/tiered")
        self.assertEqual(estimate["source"], "official")
        self.assertEqual(estimate["amount"], 0.13)
        self.assertEqual((estimate["low"], estimate["high"]), (0.13, 0.52))

    def test_official_price_respects_quantity(self):
        self._write_official({"ep/video": {
            "billing": "per_second", "unit_cny": 0.69, "unit_label": "秒",
        }})
        estimate = main.runninghub_estimate_for_profile("legacy-shared", "model", "ep/video", quantity=8)
        self.assertAlmostEqual(estimate["amount"], 5.52)

    def test_admin_table_overrides_official(self):
        self._write_official({"ep/a": {"billing": "per_unit", "unit_cny": 0.13}})
        main.save_runninghub_price_table({"models": {"ep/a": {"billing": "fixed", "unit_cny": 0.99}}})
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        estimate = main.runninghub_estimate_for_profile("legacy-shared", "model", "ep/a")
        self.assertEqual(estimate["amount"], 0.99)
        self.assertEqual(estimate["source"], "table")

    def test_seed_beats_official_for_measured_entry(self):
        # 图层拆分我们真扣过 ¥0.19，官方列表价是一段区间，实测值更贴近账单。
        self._write_official({"seedream-v5.5/layer-decomposition": {
            "billing": "per_unit", "unit_cny": 0.05, "low_cny": 0.05, "high_cny": 0.52,
        }})
        estimate = main.runninghub_estimate_for_profile(
            "legacy-shared", "model", "seedream-v5.5/layer-decomposition"
        )
        self.assertEqual(estimate["amount"], 0.19)

    def test_admin_read_never_contains_official_entries(self):
        self._write_official({"ep/a": {"billing": "per_unit", "unit_cny": 0.13}})
        self.assertEqual(main.load_runninghub_admin_price_table()["models"], {})
        # 保存一次空表也不能把官方价固化进 data/runninghub_prices.json。
        main.save_runninghub_price_table({"models": {}})
        written = json.loads(Path(self.table_path).read_text(encoding="utf-8"))
        self.assertEqual(written["models"], {})

    def test_missing_official_file_falls_back_to_unknown(self):
        estimate = main.runninghub_estimate_for_profile("legacy-shared", "model", "ep/a")
        self.assertEqual(estimate["confidence"], "unknown")
        self.assertEqual(estimate["source"], "unknown")


class ChannelModelPriceTests(unittest.TestCase):
    """渠道版模型（nano-banana / gpt-image-2 等）按官网 SKU 命名命中官方价。"""

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.table_path = str(Path(self.temp_dir.name) / "prices.json")
        self.stats_path = str(Path(self.temp_dir.name) / "stats.json")
        self.official_path = str(Path(self.temp_dir.name) / "official.json")
        self.patchers = [
            patch.object(main, "RUNNINGHUB_PRICE_TABLE_FILE", self.table_path),
            patch.object(main, "RUNNINGHUB_PRICE_STATS_FILE", self.stats_path),
            patch.object(main, "RUNNINGHUB_OFFICIAL_PRICES_FILE", self.official_path),
        ]
        for patcher in self.patchers:
            patcher.start()
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def tearDown(self):
        for patcher in reversed(self.patchers):
            patcher.stop()
        self.temp_dir.cleanup()
        main.RUNNINGHUB_ESTIMATE_CACHE.clear()
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def _write_official(self, models):
        Path(self.official_path).write_text(
            json.dumps({"schema_version": 1, "models": models}, ensure_ascii=False),
            encoding="utf-8",
        )
        main.RUNNINGHUB_OFFICIAL_PRICE_CACHE.update({"mtime": None, "models": {}})

    def test_model_price_keys_expand_tier_suffix(self):
        self.assertEqual(
            rp.model_price_keys("nano-banana/text-to-image-channel-low-price"),
            [
                "nano-banana/text-to-image-channel-low-price",
                "nano-banana/text-to-image/economy",
                "nano-banana/text-to-image/stable",
            ],
        )
        self.assertEqual(
            rp.model_price_keys("gpt-image-2/text-to-image-official-stable")[1],
            "gpt-image-2/text-to-image/stable",
        )
        # 不带档位后缀的端点只回自己，避免乱猜。
        self.assertEqual(rp.model_price_keys("seedream-v5.5/text-to-image"),
                         ["seedream-v5.5/text-to-image"])
        self.assertEqual(rp.model_price_keys(""), [])

    def test_channel_model_hits_economy_official_price(self):
        self._write_official({"nano-banana/text-to-image/economy": {
            "billing": "per_unit", "unit_cny": 0.07, "unit_label": "张", "note": "官方标价（国际站渠道）",
        }})
        estimate = main.runninghub_estimate_for_profile(
            "legacy-shared", "model", "nano-banana/text-to-image-channel-low-price"
        )
        self.assertEqual(estimate["source"], "official")
        self.assertEqual(estimate["amount"], 0.07)

    def test_official_stable_model_hits_stable_price(self):
        self._write_official({"nano-banana/text-to-image/stable": {
            "billing": "per_unit", "unit_cny": 0.2, "unit_label": "张",
        }})
        estimate = main.runninghub_estimate_for_profile(
            "legacy-shared", "model", "nano-banana/text-to-image-official-stable"
        )
        self.assertEqual(estimate["amount"], 0.2)

    def test_alias_endpoint_key_still_resolves(self):
        # 管理员/官方价目也可能以实际调用端点（rhart-*）为键，别名要能兜住。
        self._write_official({"rhart-image-g-2/text-to-image": {
            "billing": "per_unit", "unit_cny": 0.1, "unit_label": "次",
        }})
        estimate = main.runninghub_estimate_for_profile(
            "legacy-shared", "model", "gpt-image-2.0/text-to-image-channel-low-price"
        )
        self.assertEqual(estimate["amount"], 0.1)

    def test_estimate_cache_keeps_quantity_apart(self):
        self._write_official({"ep/video": {
            "billing": "per_second", "unit_cny": 0.5, "unit_label": "秒",
        }})
        four = main.runninghub_estimate_for_profile("legacy-shared", "model", "ep/video", 4)
        one = main.runninghub_estimate_for_profile("legacy-shared", "model", "ep/video")
        self.assertAlmostEqual(four["amount"], 2.0)
        self.assertAlmostEqual(one["amount"], 0.5)


class AdminPriceRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_put_validates_before_writing(self):
        payload = main.RunningHubPriceTableRequest(table={"models": {"ep": {"billing": "per_unit", "unit_cny": -1}}})
        with patch.object(main, "require_admin", return_value=None), \
             patch.object(main, "RUNNINGHUB_PRICE_TABLE_FILE", str(Path(tempfile.mkdtemp()) / "prices.json")):
            with self.assertRaises(main.HTTPException) as ctx:
                await main.runninghub_prices_put(payload, request=None)
        self.assertEqual(ctx.exception.status_code, 400)


class RunButtonMarkupTests(unittest.TestCase):
    """运行按钮与估价徽标的静态接线：任何一处漏掉，按钮上就不会出现金额。"""

    @classmethod
    def setUpClass(cls):
        root = Path(__file__).resolve().parent.parent
        cls.html = (root / "static" / "smart-canvas.html").read_text(encoding="utf-8")
        cls.badge_js = (root / "static" / "js" / "price-badge.js").read_text(encoding="utf-8")
        cls.canvas_js = (root / "static" / "js" / "smart-canvas.js").read_text(encoding="utf-8")
        cls.css = (root / "static" / "css" / "smart-canvas.css").read_text(encoding="utf-8")

    def test_price_badge_loads_before_canvas_script(self):
        # 曾经因为顺序颠倒，attach 时 RHPriceBadge 还不存在，可选链静默跳过 → 一个金额都不显示。
        self.assertIn('src="/static/js/price-badge.js', self.html)
        self.assertLess(self.html.index("js/price-badge.js"), self.html.index("js/smart-canvas.js"))

    def test_run_button_wrapped_in_price_pill(self):
        # 参考 UI：一个胶囊里左边 ✦金额、右边白色圆形箭头。
        self.assertIn('class="run-pill"', self.html)
        self.assertIn('id="runBtn" class="run-btn"', self.html)
        self.assertLess(self.html.index('class="run-pill"'), self.html.index('id="runBtn"'))
        # 按钮本身只剩图标，文字标签走 aria-label（i18n label），避免胶囊里塞文字。
        run_tag = self.html[self.html.index('id="runBtn"'):]
        run_tag = run_tag[:run_tag.index("</button>")]
        self.assertNotIn('data-i18n="smart.run"', run_tag)
        self.assertIn('data-i18n-label="smart.run"', run_tag)

    def test_badge_sits_inside_pill_and_toggles_frame(self):
        self.assertIn('anchor.parentElement', self.badge_js)
        self.assertIn('badge.closest(".run-pill")', self.badge_js)
        self.assertIn('classList.add("has-price")', self.badge_js)

    def test_prompt_length_anchor_skips_pill(self):
        # 字数提示必须插在胶囊之前；直接拿 #runBtn 当 reference 会抛 NotFoundError。
        self.assertIn("actions.querySelector('.run-pill') || actions.querySelector('#runBtn')", self.canvas_js)

    def test_pill_styles_present(self):
        self.assertIn(".run-pill {", self.css)
        self.assertIn(".run-pill.has-price", self.css)
        self.assertIn(".run-pill .rh-price-badge {", self.css)

    def test_badge_sends_resolution(self):
        # 分辨率要随请求发给后端，否则 2K 与 3K 会共用同一档位价。
        self.assertIn('params.set("res"', self.badge_js)
        self.assertIn("runningHubPriceResolution", self.canvas_js)

    def test_badge_attach_is_idempotent(self):
        # 重复 attach 会插出第二个徽标、且旧闭包停在过期参数上，金额看起来永远不变。
        self.assertIn("anchor._rhPriceAttached", self.badge_js)
        self.assertIn("if (existing) return existing;", self.badge_js)

    def test_run_state_sync_refreshes_price_spec(self):
        # 切节点时 syncRunButtonState(subject) 不带参数，composerActionNode() 会回退到
        # 画布选中节点，报价就会停在上一个节点上。同步状态后必须再刷新一次徽标。
        self.assertIn("dispatchEvent(new Event('smart-run-state-sync'))", self.canvas_js)
        self.assertIn("addEventListener('smart-run-state-sync'", self.canvas_js)

    def test_price_model_id_falls_back_to_provider_list(self):
        # 节点残留的 model 不在该 provider 清单里时，估价退回清单首项，避免查到一个永远没价的键。
        self.assertIn("function runningHubPriceModelId", self.canvas_js)
        self.assertIn("configured.includes(current)", self.canvas_js)


class ResolutionTierTests(unittest.TestCase):
    """官方分档价（1.5K/2K/3K）按输出像素收敛成单一档位。"""

    def tiers(self):
        return rp.parse_tier_notes(
            {
                "输出图 1.5K": 0.13,
                "输出图 2K": 0.23,
                "输出图 3K": 0.52,
            }
        )

    def test_megapixels_matches_floor_unit(self):
        # 阈值与输入必须是同一量纲（百万像素），否则所有档位都会退到最低价。
        tiers = self.tiers()
        self.assertAlmostEqual(tiers[0][0], 2.359296, places=5)
        self.assertEqual(rp.pick_tier_amount(tiers, "2k"), 0.23)
        self.assertEqual(rp.pick_tier_amount(tiers, "3k"), 0.52)
        self.assertEqual(rp.pick_tier_amount(tiers, "1k"), 0.13)

    def test_tier_notes_from_tiers_list(self):
        notes = rp._tier_notes(
            [{"label": "输出图 2K", "price": 0.23}, {"label": "输出图 3K", "price": 0.52}]
        )
        self.assertEqual(notes, {"输出图 2K": 0.23, "输出图 3K": 0.52})

    def test_wan_pixel_threshold_not_confused_by_money(self):
        # “260万像素”里的“万元”是金额单位，不能被读成 2600000 元。
        tiers = rp.parse_tier_notes(
            {"单张 ≤260万像素，含 1K、1.5K 档": 0.13, "单张 >462万像素": 0.52}
        )
        self.assertEqual(tiers, [(2.6, 0.13), (4.62, 0.52)])

    def test_unknown_resolution_keeps_range(self):
        tiers = self.tiers()
        self.assertIsNone(rp.pick_tier_amount(tiers, ""))
        self.assertIsNone(rp.pick_tier_amount(tiers, "auto"))

    def test_fit_resolution_collapses_range(self):
        table = {
            "models": {
                "m/text-to-image": {
                    "billing": "per_unit",
                    "unit_cny": 0.13,
                    "low_cny": 0.13,
                    "high_cny": 0.52,
                    "source": rp.SOURCE_OFFICIAL,
                    "notes": {"输出图 1.5K": 0.13, "输出图 2K": 0.23, "输出图 3K": 0.52},
                }
            }
        }
        raw = rp.estimate_price(table, None, "model", "m/text-to-image")
        self.assertEqual((raw["low"], raw["high"]), (0.13, 0.52))
        fitted = rp.fit_resolution(raw, "2k")
        self.assertEqual(fitted["amount"], 0.23)
        self.assertEqual((fitted["low"], fitted["high"]), (0.23, 0.23))
        kept = rp.fit_resolution(raw, "")
        self.assertEqual((kept["low"], kept["high"]), (0.13, 0.52))
        self.assertIn("_tiers", kept)


class ModelNameFallbackTests(unittest.TestCase):
    """只收敛同一模型的写法差异；不同型号（Dola / Seedream）绝不互相顶替价格。"""

    def test_normalize_aliases_operation_names(self):
        self.assertEqual(
            rp._normalize_model_name("nano-banana/edit"),
            rp._normalize_model_name("nano-banana/image-to-image"),
        )
        self.assertEqual(
            rp._normalize_model_name("dola-seedream-5.5/text-to-image"),
            rp._normalize_model_name("Dola-Seedream-5.5/text-to-image"),
        )

    def test_variants_only_case_changes(self):
        # 官网 Dola Seedream 5.5 与 Seedream V5.5 是两条独立 SKU，
        # 即便价目相同也不能通过剥前缀互换。
        # 大小写归一只用于更宽容地命中同一条 SKU，不剥型号前缀。
        self.assertEqual(
            rp.model_name_variants("Dola-Seedream-5.5/text-to-image"),
            ["dola-seedream-5.5/text-to-image"],
        )
        self.assertEqual(rp.model_name_variants("seedream-v5.5/text-to-image"), [])

    def test_loose_keys_never_cross_models(self):
        # Dola 与 Seedream 价格一样，但不是同一个模型，不许借用。
        table = {"models": {"seedream-v5.5/text-to-image": {"unit_cny": 0.13}}}
        self.assertEqual(rp.loose_model_keys(table, ["Dola-Seedream-5.5/text-to-image"]), [])

    def test_loose_keys_match_case_and_separators(self):
        table = {"models": {"dola-Seedream-5.5/text2image": {"unit_cny": 0.13}}}
        self.assertEqual(
            rp.loose_model_keys(table, ["Dola-Seedream-5.5/text-to-image"]),
            ["dola-Seedream-5.5/text2image"],
        )

    def test_loose_keys_ignore_different_model(self):
        table = {"models": {"seedream-v5.5/image-to-image": {"unit_cny": 0.05}}}
        self.assertEqual(
            rp.loose_model_keys(table, ["Dola-Seedream-5.5/text-to-image"]), []
        )


class DisplayNameEndpointTests(unittest.TestCase):
    """画布上存的是展示名（MiniMax-H3 ...），官方价目表用端点名，必须能对上。"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.registry_path = str(Path(self.tmpdir) / "models_registry.json")
        Path(self.registry_path).write_text(
            json.dumps(
                {
                    "models": [
                        {
                            "endpoint": "minimax/hailuo-h3/image-to-video",
                            "name_en": "MiniMax-H3 Image-to-Video (first/last frame)",
                            "name_cn": "MiniMax-H3 图生视频（首尾帧）",
                        },
                        {
                            "endpoint": "rhart-image-flux3-klein/text-to-image",
                            "name_en": "flux-3-image-klein/text-to-image",
                            "name_cn": "FLUX 3 Image Klein 文生图",
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        self.patch = patch.object(
            main, "STATIC_RUNNINGHUB_MODEL_REGISTRY_FILE", self.registry_path
        )
        self.patch.start()
        main.RUNNINGHUB_REGISTRY_SNAPSHOT_CACHE.update({"mtime": None, "items": []})

    def tearDown(self):
        self.patch.stop()
        main.RUNNINGHUB_REGISTRY_SNAPSHOT_CACHE.update({"mtime": None, "items": []})

    def test_display_name_maps_to_endpoint(self):
        self.assertEqual(
            main.runninghub_model_entry_id("MiniMax-H3 图生视频（首尾帧）"),
            "minimax/hailuo-h3/image-to-video",
        )
        self.assertEqual(
            main.runninghub_model_entry_id("MiniMax-H3 Image-to-Video (first/last frame)"),
            "minimax/hailuo-h3/image-to-video",
        )

    def test_model_id_passes_through(self):
        self.assertEqual(
            main.runninghub_model_entry_id("flux-3-image-klein/text-to-image"),
            "rhart-image-flux3-klein/text-to-image",
        )

    def test_unknown_name_falls_back_to_itself(self):
        # 注册表里没有的名字不做猜测，原样返回，查不到价就继续显示待定。
        self.assertEqual(
            main.runninghub_model_entry_id("some-unlisted-model/text-to-image"),
            "some-unlisted-model/text-to-image",
        )

    def test_app_and_workflow_have_no_price_key(self):
        self.assertEqual(main.runninghub_model_entry_id("app:123"), "")
        self.assertEqual(main.runninghub_model_entry_id("workflow:456"), "")


class OfficialTierParsingTests(unittest.TestCase):
    """官网价目表解析：输入图单价、分组小标题、计价系数都不能当成生成本身的价格。"""

    def _entry(self, rows, simple=None, simple_text=""):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "sync_off", Path(__file__).resolve().parents[1] / "tools" / "sync_runninghub_official_prices.py"
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        table = {"rows": [{"cells": row} for row in rows]}
        if simple_text:
            return mod, mod.entry_from_sku(
                {"id": "1", "name": "x", "price": {"price": simple, "simplePriceText": simple_text}},
                mod._pick_tiers({}, table),
            )
        return mod, mod.entry_from_sku(
            {"id": "1", "name": "x", "price": {"price": simple, "simplePriceText": simple_text}},
            mod._pick_tiers({}, table),
        )

    def test_input_image_price_is_not_the_generation_price(self):
        _, entry = self._entry([
            ["输入图", "¥0.018/张", "首张免费，超出部分按张计费"],
            ["输出图（≤236万像素）", "¥0.27/张", ""],
            ["输出图（>236万像素）", "¥0.54/张", ""],
        ])
        self.assertEqual(entry["low_cny"], 0.27)
        self.assertEqual(entry["high_cny"], 0.54)
        self.assertNotIn("输入图", entry["notes"])

    def test_header_row_is_group_label_not_a_tier(self):
        _, entry = self._entry([
            ["Proteus系列（常规）", "", "", ""],
            ["1s", "¥0.56", "¥0.56", "¥0.56"],
            ["5s", "¥0.56", "¥0.56", "¥1.68"],
        ])
        labels = [tier["label"] for tier in entry["tiers"]]
        self.assertNotIn("Proteus系列（常规）", labels)
        self.assertIn("Proteus系列（常规） · 1s", labels)

    def test_billing_coefficient_is_dropped(self):
        _, entry = self._entry([
            ["720P", "¥0.51/秒", "按秒计费"],
            ["1080P", "¥0.85/秒", "按秒计费"],
            ["", "¥0.001/次", "计价系数"],
        ])
        self.assertEqual(entry["low_cny"], 0.51)
        self.assertEqual(entry["high_cny"], 0.85)

    def test_price_in_note_column_is_picked_up(self):
        _, entry = self._entry([
            ["按视频时长计费，", "按音频时长计费", "¥0.016/秒，总价=单价×billingSeconds"],
        ])
        self.assertIsNotNone(entry)
        self.assertEqual(entry["low_cny"], 0.016)
        self.assertEqual(entry["billing"], "per_second")

    def test_frame_unit_is_understood(self):
        _, entry = self._entry([
            ["Proteus系列（常规）", "", ""],
            ["帧价", "约¥0.0217/帧", ""],
        ])
        self.assertEqual(entry["unit_label"], "帧")


if __name__ == "__main__":
    unittest.main()
