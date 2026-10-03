"""Laya (Convai Innovations) Non-Autoregressive System 1 Decision Model Adapter.

Provides memory-efficient in-place streaming inference for Laya ModernBERT-large
decision models on CPU/GPU without triggering Windows memory-mapping paging limits.

Guarantees:
- Single parallel forward pass (0 output tokens generated).
- Mathematically calibrated probabilities over discrete choices.
- Supports 'choice', 'score', and 'noul' question types.
- Pure decision engine (execution_authority=False).
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union

import torch
from safetensors import safe_open
from transformers import AutoConfig, AutoModel, AutoTokenizer

from laya.agent import Agent
from laya.common import DecisionModel

log = logging.getLogger("LayaDecisionModel")


@dataclass
class LayaDecisionResult:
    """Structured decision output from Laya System 1 forward pass."""
    answers: Dict[str, Any]
    probabilities: Dict[str, Dict[str, float]]
    confidences: Dict[str, float]
    latency_ms: float
    input_tokens: int
    output_tokens: int = 0
    model: str = "laya-modernbert-large"


class LayaDecisionModel:
    """Production-hardened runtime wrapper for Laya decision checkpoints."""

    def __init__(
        self,
        model_dir: str = "models/laya",
        device: Optional[str] = None,
        use_typed_decisions: bool = False,
    ) -> None:
        self.model_dir = os.path.abspath(model_dir)
        if use_typed_decisions:
            sub = os.path.join(self.model_dir, "typed-decisions")
            if os.path.exists(sub):
                self.model_dir = sub

        self.device = torch.device(device if device else ("cuda" if torch.cuda.is_available() else "cpu"))
        self._load_agent()

    def _load_agent(self) -> None:
        """Stream weights in-place into the parameter dtype to avoid Windows pagefile saturation.

        dtype policy: bfloat16 on GPU, float32 on CPU. fp16 on CPU is orders of
        magnitude slower (unoptimized fallback kernels — measured 165s per
        forward pass vs ~2s fp32), which stalled the whole boot pipeline inside
        news triage. The streaming safe_open loop below keeps peak memory at
        one tensor regardless of dtype.
        """
        t0 = time.perf_counter()
        agent = Agent.__new__(Agent)
        agent.device = self.device
        param_dtype = torch.bfloat16 if self.device.type != "cpu" else torch.float32
        agent.dtype = param_dtype

        cfg_path = os.path.join(self.model_dir, "rl_agent_config.json")
        if not os.path.exists(cfg_path):
            cfg_path = os.path.join(os.path.dirname(self.model_dir), "rl_agent_config.json")
        agent.cfg = json.load(open(cfg_path, encoding="utf-8"))

        tok_path = os.path.join(self.model_dir, "tokenizer")
        if not os.path.exists(tok_path):
            tok_path = os.path.join(os.path.dirname(self.model_dir), "tokenizer")
        agent.tok = AutoTokenizer.from_pretrained(tok_path)

        enc_path = os.path.join(self.model_dir, "encoder")
        if not os.path.exists(enc_path):
            enc_path = os.path.join(os.path.dirname(self.model_dir), "encoder")
        ecfg = AutoConfig.from_pretrained(enc_path)

        enc = AutoModel.from_config(ecfg, dtype=param_dtype, attn_implementation="sdpa")
        agent.model = DecisionModel(
            enc,
            head_layers=agent.cfg.get("head_layers", 2),
            n_act=len(agent.cfg.get("act_costs", {})) + 1,
        ).to(param_dtype)

        weights_path = os.path.join(self.model_dir, "model.safetensors")
        params = dict(agent.model.named_parameters())
        buffers = dict(agent.model.named_buffers())

        with safe_open(weights_path, framework="pt", device=str(self.device)) as f:
            for k in f.keys():
                t = f.get_tensor(k)
                if k in params:
                    params[k].data.copy_(t.to(param_dtype))
                elif k in buffers:
                    buffers[k].data.copy_(t.to(param_dtype))

        agent.temperature = agent.cfg.get("temperature", [1.0, 1.0, 1.0])
        agent.temperature_by_options = agent.cfg.get("temperature_by_options", {})
        # act_head receives float32 concatenated features in forward()
        if any(p.dtype != torch.float32 for p in agent.model.act_head.parameters()):
            agent.model.act_head = agent.model.act_head.to(torch.float32)
        agent.model.eval()

        self.agent = agent
        elapsed = (time.perf_counter() - t0) * 1000
        log.info("Loaded Laya model from %s in %.1f ms", self.model_dir, elapsed)

    def decide(
        self,
        state: Union[str, Dict[str, Any]],
        questions: Dict[str, Dict[str, Any]],
    ) -> LayaDecisionResult:
        """Run single-pass System 1 decision inference across typed questions."""
        t0 = time.perf_counter()
        raw = self.agent.predict(state, questions)
        latency = (time.perf_counter() - t0) * 1000

        answers: Dict[str, Any] = {}
        probabilities: Dict[str, Dict[str, float]] = {}
        confidences: Dict[str, float] = {}

        for qid, q_out in raw.get("answers", {}).items():
            q_type = q_out.get("type")
            if q_type == "choice":
                answers[qid] = q_out.get("choice")
                probabilities[qid] = q_out.get("probabilities", {})
            elif q_type == "score":
                answers[qid] = q_out.get("score")
                probabilities[qid] = q_out.get("probabilities", {})
            elif q_type == "noul":
                answers[qid] = bool(q_out.get("noul", 0.0) >= 0.5)
                prob_true = float(q_out.get("noul", 0.0))
                probabilities[qid] = {"false": round(1.0 - prob_true, 4), "true": round(prob_true, 4)}

            confidences[qid] = float(q_out.get("confidence", 0.0))

        usage = raw.get("usage", {})
        return LayaDecisionResult(
            answers=answers,
            probabilities=probabilities,
            confidences=confidences,
            latency_ms=round(latency, 2),
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
        )


_global_laya: Optional[LayaDecisionModel] = None


def get_laya_decision_model(model_dir: str = "models/laya") -> LayaDecisionModel:
    """Singleton getter for cached LayaDecisionModel instance."""
    global _global_laya
    if _global_laya is None:
        _global_laya = LayaDecisionModel(model_dir=model_dir)
    return _global_laya
