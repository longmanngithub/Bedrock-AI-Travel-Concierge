---
name: security-review
description: Review project source and configuration for authentication, authorization, data isolation, input handling, dependency, and deployment risks. Use for defensive code reviews and remediation planning.
---

# Source-based security review

1. Establish the scope and trace the affected trust boundaries from entry point to protected resource. Check which principal owns the resource, where permissions are enforced, and whether background jobs, caches, and alternate routes apply the same checks.
2. Read the relevant existing tests, dependency manifests, and configuration as evidence. Inspect source and configuration only. Keep target systems, credentials, and real user data out of the review.
3. For each finding, point to the affected files and lines. Explain the violated invariant, the conditions required, the likely impact, and the smallest effective fix. Verify that route guards, data filters, or other controls do not already prevent the issue.
4. Distinguish source-confirmed issues, candidates that need validation, and rejected candidates. State which paths were reviewed, the basis for the verdicts, and any remaining evidence gaps. This workflow is a source review.

## Optional schema checks

This directory includes the MIT-licensed Cloudflare report schema and zero-dependency Node.js validators from commit `c1c8a8c1471069fb0e188eeaff69b8e8db6564a8`. They check the format of compatible JSON reports. Use them only for compatible artifacts already available or the bundled synthetic fixtures.

From this skill's directory, run `node --test validate-findings.test.cjs validate-coverage-ledger.test.cjs` to exercise both validators with the bundled fixtures.
