"""See/Think/Act/Reflect phases, Propose/Verify/Commit stages, and governance counters."""
import unittest
from pathlib import Path

from pydantic import TypeAdapter

from app.agent import KYCExceptionAgent
from app.domain import AgentDecision
from app.workflow.nodes import GOV_STAGE_BY_STEP, PHASE_BY_STEP
from evals.planners import CompromisedEvalPlanner, PolicyMatchingEvalPlanner

ROOT = Path(__file__).resolve().parents[1]


class PhaseTaggingTests(unittest.TestCase):
    def test_every_graph_node_has_a_phase(self) -> None:
        from app.tools import DomainTools
        from app.workflow.graph import build_workflow_graph

        graph = build_workflow_graph(DomainTools(), PolicyMatchingEvalPlanner())
        nodes = {n for n in graph.get_graph().nodes if not n.startswith("__")}
        self.assertEqual(nodes - set(PHASE_BY_STEP), set())
        self.assertEqual(set(PHASE_BY_STEP.values()), {"SEE", "THINK", "ACT", "REFLECT"})

    def test_all_trace_events_carry_phase_and_governed_steps_carry_stage(self) -> None:
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1042", planner=PolicyMatchingEvalPlanner())
        approved = agent.approve(first.pending_task.interrupt_key)
        finished = agent.resume(approved.pending_task.interrupt_key, {"documents": [{"type": "proof_of_address", "status": "verified"}]})
        for event in finished.trace:
            self.assertIsNotNone(event.phase, event.step)
            self.assertEqual(event.gov_stage, GOV_STAGE_BY_STEP.get(event.step), event.step)
        stages = [e.gov_stage for e in finished.trace if e.gov_stage]
        self.assertEqual(stages[:4], ["PROPOSE", "VERIFY", "COMMIT", "COMMIT"])
        self.assertEqual(finished.trace[-1].phase, "REFLECT")


class GovernanceCounterTests(unittest.TestCase):
    def test_counts_across_a_two_cycle_run(self) -> None:
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1042", planner=PolicyMatchingEvalPlanner())
        self.assertEqual(first.governance, {"reads": 4, "proposals": 1, "verifications": 1, "overrides": 0,
                                            "approvals": 0, "rejections": 0, "writes": 0})
        approved = agent.approve(first.pending_task.interrupt_key)
        finished = agent.resume(approved.pending_task.interrupt_key, {"documents": [{"type": "proof_of_address", "status": "verified"}]})
        g = finished.governance
        self.assertEqual((g["reads"], g["proposals"], g["approvals"], g["writes"]), (8, 2, 1, 1))

    def test_override_and_rejection_are_counted(self) -> None:
        blocked = KYCExceptionAgent().run("KYC-1044", planner=CompromisedEvalPlanner())
        self.assertEqual((blocked.governance["overrides"], blocked.governance["writes"]), (1, 0))
        agent = KYCExceptionAgent()
        first = agent.run("KYC-1046", planner=PolicyMatchingEvalPlanner())
        rejected = agent.reject(first.pending_task.interrupt_key, "Customer withdrew")
        self.assertEqual((rejected.governance["rejections"], rejected.governance["writes"]), (1, 0))

    def test_http_payload_round_trips_new_fields(self) -> None:
        decision = KYCExceptionAgent().run("KYC-1043", planner=PolicyMatchingEvalPlanner())
        body = decision.to_dict()
        restored = TypeAdapter(AgentDecision).validate_python(body)
        self.assertEqual(restored.governance, decision.governance)
        self.assertEqual(restored.rule["id"], "R-ID-EXP-01")
        self.assertEqual({e.phase for e in restored.trace} - {None}, {"SEE", "THINK"})


class StaticUISurfaceTests(unittest.TestCase):
    def test_static_ui_renders_phases_governance_and_rule(self) -> None:
        source = (ROOT / "static" / "index.html").read_text()
        for needle in ("const PHASES=", "REFLECT:['finalize']", "function govStrip(", "function ruleCard(",
                       "function govBadges(", "d.ontology_path", "expertRule", "e.gov_stage"):
            self.assertIn(needle, source)
        self.assertNotIn("finalize_blocked", source)


if __name__ == "__main__":
    unittest.main()
