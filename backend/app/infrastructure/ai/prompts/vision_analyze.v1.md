Analyse the attached photographs of the user's situation.

## The user's intent

Workflow: {workflow}
In their words: "{intent}"
Preferred units: {unit_system}

## Fields to report

{requested_fields}

## Wall-material cues

Use these curated cues when judging `wall.material`. If none of them match what
you can see, return `null` rather than the closest option — an incorrect wall
material leads to an unsafe anchor recommendation.

{material_hints}

## How to work through the images

1. Identify the product: category, brand, model. Read any visible label rather
   than inferring from appearance.
2. Read the specification label if one is visible: weight, dimensions, mounting
   pattern.
3. If a mount or bracket is present, identify it and read its rated load.
4. Examine the installation surface. Report the material only if you can see a
   cue that supports it, and set `bbox` around the evidence.
5. Look for obstacles and services: sockets, switches, visible conduit, pipework,
   vents, existing holes or patches.
6. Look for missing hardware: an incomplete bag of fixings, a bracket with empty
   holes, a part that appears absent from the layout.
7. Flag the `task.*` hazard fields based on what the work will involve.
8. Estimate the working height in metres if the position is visible.

For any field you cannot determine from the images, return `null` with confidence
`0.0` and, where useful, a short `note` saying what additional photograph would
settle it.
