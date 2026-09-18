"""Content pipeline: exam protocol PDFs → AI extraction → doctor review → owner validation → gold.

A bounded context separate from the learner application. It owns the `content` database
and only touches the platform through the clinical registry when a gold protocol becomes
training bundles.
"""
