# Prompt To Board Flow

1. User fills structured prompt sections:
   - Goal
   - Must-have rules
   - Optional confirmations
   - Market/universe
   - Timeframes
   - Risk and alert preferences
   - Things to avoid
   - Pasted notes
   - Extra instructions

2. Dashboard calls:

   `POST /api/v1/dashboard/strategies/interpret`

3. Backend returns:
   - Strategy draft.
   - Prompt coverage report.
   - Interpreted rules.
   - Assumptions.
   - Ambiguities.
   - Unsupported/provider-required items.
   - Ignored filler.
   - Confidence score.
   - Visual diff.

4. User reviews the Understanding Preview.

5. User can mark feedback:
   - This is correct
   - Wrong timeframe
   - Missed a condition
   - Wrong direction
   - Too strict
   - Too loose
   - Start over

6. Feedback is stored through:

   `POST /api/v1/dashboard/strategies/interpret/feedback`

7. If not blocked, user opens the same Strategy Board used by template and visual paths.

8. User validates before activation.

9. Start Monitoring saves the version and publishes it.
