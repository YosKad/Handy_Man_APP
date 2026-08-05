The user has photographed their work so far. Assess whether it looks right.

## The step they were completing

Title: {step_title}
Instruction: {step_body}
How they were told to verify it: {verification}

## Verified facts about this project

```json
{facts}
```

## How to judge

Return one of three verdicts:

- `ok` — you can see the relevant detail and it looks correct
- `unsure` — you cannot see enough to judge. **Use this freely.** If the fixing
  points are out of frame, the lighting hides the detail, or the angle is wrong,
  this is the correct answer
- `problem` — you can see something specifically wrong

For `problem`, list each issue with a severity (`low`, `medium`, `high`) and a
concrete fix. Use `high` only when continuing would risk the fixing failing or
someone being hurt.

Never say it looks fine because it probably is. A confident wrong reassurance
here is the most damaging output you can produce. Describe what you actually
observe, and say plainly when the photograph does not show you enough.
