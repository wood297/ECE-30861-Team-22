"""Automated test runner for the Cybersecurity Interceptor pod API.

suite.py   - the cross-team acceptance suite (AC-01..AC-08 plus interface checks)
client.py  - HTTP clients the suite can drive (remote URL or in-process WSGI app)
session.py - preparing a target system through its POST /test/setup
service.py - the tester web service: POST /test/setup, POST /test/run, GET /test/report
__main__   - command-line entry point: python -m test_runner --url <base url>
"""
