# Contributing

1. Keep browser imports and assets inside this repository.
2. Do not commit datasets, run outputs, injected memory, reports, or secrets.
3. Keep `app.js` and `styles.css` as the single browser entrypoints.
4. Put API and workflow behavior in `src/action/`.
5. Put HTML composition in `src/render/`.
6. Run `node scripts/validate.mjs` before submitting changes.

Changes to the compatible API contract must also update `docs/api-contract.md`
and the relevant payload smoke tests.
