# Daily AI Frontier

This project creates a premium-looking AI news dashboard that updates every day at 9:00 AM China time by using GitHub Actions, Gemini, and public RSS sources.

## What it does

- Collects up to 20 fresh AI items from major media and research sources
- Uses Gemini to rewrite them into concise Chinese summaries
- Builds a polished static website with large visuals and premium styling
- Publishes the generated site from the `docs` folder

## Files you need

- `generate_digest_site.py`: the main generator
- `requirements.txt`: Python dependencies
- `.github/workflows/daily-frontier-ai.yml`: the daily scheduler
- `docs/`: generated website output

## Required GitHub secret

- `GEMINI_API_KEY`

## First run

After adding the files and secret:

1. Open the `Actions` tab
2. Run `Daily AI Frontier`
3. Wait for it to finish
4. Go to `Settings -> Pages`
5. Set source to `Deploy from a branch`
6. Choose branch `main`
7. Choose folder `/docs`
8. Save

Your website will then update every day automatically.
