# Skill: prompt-refiner (animate pipeline)

Guidance injected into the spec-compilation step. It shapes HOW the agent turns a user
prompt into a `SceneSpec`. It does not change the schema — validation still rules.

## Direction
- **Open on a hook.** scenes[0] is always a `hook_claim`/`hook_question`/`pattern_interrupt`,
  <= 3s, that creates curiosity or stakes. Never open on a title.
- **One idea per scene.** Don't cram; let each beat breathe.
- **End with a payoff + CTA.** Close on `celebration` or `end_card` with a clear takeaway.
- **Match pacing to duration.** Short (<=15s): 3-5 scenes. Long (30-60s): 6-10 scenes.
- **Plain, punchy copy.** Short labels. ASCII only. No jargon, no filler.

## Choosing scene archetypes
- Steps / process → `arrow_flow` (2-4 steps).
- A list of features/tips → `boxes_popin` (2-6 short labels).
- A single bold statement → `title_writeon`.
- Sports play (a shot, a tip, a dunk) → `basketball_tip` + a `scoreboard` for the result.
- Energy / a win / a launch → `celebration`.
- Motion / a journey → `object_hop` or `camera_pan`.

## Captions
Every scene gets a short `caption` (muted autoplay dominates). Keep it < 8 words.

## Few-shot (a clean, valid spec)
{"title":"3 ways to save","fps":30,"aspect":"9:16","total_duration_s":12.0,"scenes":[
 {"type":"hook_question","duration_s":2.5,"params":{"text":"Money vanishing each month?"},"caption":"Where does it go?"},
 {"type":"boxes_popin","duration_s":4.0,"params":{"items":["Track it","Auto-save","Cut one bill"]},"caption":"3 quick wins"},
 {"type":"celebration","duration_s":3.0,"params":{"label":"More saved!"},"caption":"Small wins add up"},
 {"type":"end_card","duration_s":2.5,"params":{"title":"Start today","subtitle":"Future you says thanks"}}]}
