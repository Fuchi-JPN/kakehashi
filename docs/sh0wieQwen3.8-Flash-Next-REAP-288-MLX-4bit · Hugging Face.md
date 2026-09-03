## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#qwen38-flash-next-reap-288-mlx-4-bit)Qwen3.8-Flash-Next REAP-288 (MLX, 4-bit)

|                          |     Disk     | Resident memory | HumanEval pass@1 | Decode (M4 Max) |
|--------------------------|--------------|-----------------|------------------|-----------------|
|  Base Q4 (512 experts)   |    98 GB     |      97 GB      |      93.9%       |    25 tok/s     |
| **This build (288 experts)** | **68 GB (-31%)** |  **39 GB (-60%)**   |      **91.5%**       |    **28 tok/s**     |

Resident figures are with the n-gram table on NVMe for this build (see below); the base Q4 has no such mode and barely fits a 128 GB Mac at all.

Qwen3.8-Flash-Next with 288 of 512 experts per MoE layer, pruned with REAP saliency calibrated on the quantized weights, on the machine that serves them. Loads on stock mlx-vlm with no patches and runs Claude Code-style agentic workloads on a 128 GB Mac with room to spare: 68 GB resident as shipped, 39 GB with the n-gram table left on NVMe.

-   180B-parameter class: 125B main model, 51B n-gram embedding table, 48 layers alternating Gated DeltaNet and Qwen sparse attention, each with a 288-expert MoE routing top-10
-   ~28 tok/s decode on an M4 Max (128 GB), MTP speculative decoding supported out of the box
-   Multimodal weights (vision tower) are intact but only text quality has been evaluated

## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#run-it)Run it

Requires mlx-vlm with `qwen4_exp` MTP support (git main after 2026-08-27, or any release that includes it):

```bash
pip install git+https://github.com/Blaizzy/mlx-vlm.git
```

Generate:

```css
python -m mlx_vlm.generate \
  --model sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit \
  --prompt "Refactor this function to add input validation." \
  --max-tokens 512
```

Serve (OpenAI-compatible):

```css
python -m mlx_vlm.server \
  --model sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit --port 8080
```

Speculative decoding with the model's own MTP head, using the companion drafter [sh0wie/Qwen3.8-Flash-Next-MTP-Drafter-MLX-bf16](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-MTP-Drafter-MLX-bf16):

```css
python -m mlx_vlm.generate \
  --model sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit \
  --draft-model sh0wie/Qwen3.8-Flash-Next-MTP-Drafter-MLX-bf16 \
  --draft-kind mtp \
  --prompt "..." --max-tokens 512
```

A note on speculative speed: the drafter's acceptance rate is healthy (~44-68% depending on sampling), but the net speedup depends on how cheaply your hardware runs the verification pass. M5-class GPUs report 1.5-2.6x; on M4 it is roughly break-even. Quality is unaffected either way, since the target model verifies every drafted token.

## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#why-288-experts)Why 288 experts

We measured an eleven-point pruning ladder and 288 is where quality-per-GB peaks among usable builds. Aggregate metrics (KL divergence against the stock model) suggest pruning deeper, but sampled rare-token reliability collapses below 288: a 256-expert build produces an intact rare name in 1 of 10 seeded generations where this build scores 9 of 10, a failure mode invisible to greedy evaluation and unrepairable by giving the experts more bits. The full study and tooling will be made available soon; the per-layer kept-expert manifest ships in this repo as `reap_kept_experts.json`, which makes the prune reproducible from the source conversion.

|    Build (experts)     | Disk  | HumanEval pass@1 |
|------------------------|-------|------------------|
| 512 (stock conversion) | 98 GB |      93.9%       |
|          384           | 80 GB |      92.1%       |
|          320           | 72 GB |      90.9%       |
|    **288 (this build)**    | **68 GB** |      **91.5%**       |
|          256           | 65 GB |      88.4%       |

All legs ran the same harness on the same machine: 164 HumanEval problems, unit-test verified, one run per build. Routing width is untouched at the trained top-10; narrowing it costs far more than it saves (top-6 scores 84.8%, top-4 collapses to 63.4%, for at most +2.5 tok/s).

## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#pinned-expert-manifests-ssd-expert-streaming)Pinned-expert manifests (SSD expert streaming)

The `manifests/` folder ships our measured per-layer saliency selection in the plain layer-id -> expert-id-list JSON shape that SSD-expert-streaming runtimes consume directly as a pinned source of truth. Expert ids are stock ids (0-511) and layer keys are the 48 routed MoE layers (0-47), so the maps pin against the full 512-expert base checkpoint. Each file is a flat `{"<layer>": [<expert ids>], ...}` map, ids only, well under the 2 MiB limit.

-   `manifests/qwen38-flash-next-512e_saliency_pinned.json` - all 512 experts per layer in descending saliency order. Pin a prefix of length K to get the top-K experts at any budget; the first 288 ids of each layer are exactly the kept set of this build.
-   `manifests/qwen38-flash-next-reap-288_kept.json` - the REAP-288 kept set, ascending ids (same selection as `reap_kept_experts.json`). The exact 288-expert case.
-   `manifests/qwen38-flash-next-reap-288_kept_saliency_ordered.json` - the same 288 kept ids reordered descending by saliency, so a shorter prefix is a valid smaller-budget selection.

Saliency is the same on-device REAP measurement used to choose this build's kept experts. The maps match the REAP/omlx manifest shape (a plain layer -> expert-id map, also accepted under `layers` / `pinned_experts` / `kept_experts` wrappers).

## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#the-39-gb-mode)The 39 GB mode

Per token the model reads only a few hundred bytes of the 51B n-gram table, so the table does not need to be resident. A row-granular disk-read patch (ours, not yet upstream in mlx-vlm) serves it from NVMe with logits bit-identical to the in-memory path, dropping resident memory from ~68 GB to ~39 GB at the same ~28 tok/s decode.

## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#decode-speed-with-and-without-pmlx)Decode speed (with and without pmlx)

The decode figures above are stock mlx-vlm today. A separate pure-MLX engine, pmlx, runs these same 4-bit weights faster; it releases alongside the upcoming tiered model. It is not required to run this build, and nothing here waits on it, but the same download gets faster when it lands.

|                Engine                 | Resident | Decode (M4 Max) |
|---------------------------------------|----------|-----------------|
|         **stock mlx-vlm (today)**         |  **39 GB**   |    **~28 tok/s**     |
| pmlx (coming with the tiered release) |  39 GB   |    ~37 tok/s    |
|  pmlx, n-gram table in RAM (coming)   |  73 GB   |    ~65 tok/s    |

Same 4-bit build in every row. The RAM-table row trades memory for throughput; neither pmlx row is needed to serve the model today.

## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#provenance-and-what-was-fixed)Provenance and what was fixed

-   `Qwen/Qwen3.8-Flash-Next`: upstream weights
-   [Sawfwair/Qwen3.8-Flash-Next-MLX-4bit](https://huggingface.co/Sawfwair/Qwen3.8-Flash-Next-MLX-4bit): MLX affine 4-bit conversion (group size 64; n-gram table group size 32)
-   This build: REAP expert pruning 512 -> 288 per layer, calibrated on-device over ~686K tokens of agentic-coding traffic

Two defects of the source conversion are corrected in the weights, so no loader patches are needed: RMSNorm tensors stored un-centered (+1) are re-centered to the zero-centered convention the runtime's `(1 + w)` norm expects, and the n-gram table tensors plus their per-tensor quantization overrides are renamed `shard_N -> shards.N` to match the runtime module path. Everything else is byte-identical to the pruned source. Stock-runtime logits on this build match our patched-runtime reference exactly (max abs diff 0.0 at the final prefill position).

## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#limitations)Limitations

-   Calibration reflects one team's agentic-coding distribution. Retention numbers should not be read as general-domain; domains far from code may degrade more.
-   Single-run evaluations, no confidence intervals. Differences of a point or two between neighboring builds are within noise.
-   Rare-name sampling reliability is 9/10, not 10/10; pruned models benefit from clearing context after a visible garbled name, since a corruption that enters the context conditions later turns.
-   Vision input is untested after pruning.

## [](https://huggingface.co/sh0wie/Qwen3.8-Flash-Next-REAP-288-MLX-4bit#license)License

Qwen Community License 1.0, inherited from the base model; see `LICENSE`.