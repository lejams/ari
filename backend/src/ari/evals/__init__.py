"""Reproducible measurement of LLM outputs: datasets, scorers, reports, gates, comparison.

`python -m ari.evals run` executes one suite on one configuration and writes a JSON report;
`python -m ari.evals compare` compares reports and recommends the cheapest configuration
among those that pass every gate. Runtime code never imports this package (import-linter).
"""
