.PHONY: ownership-probe-check ownership-probe-test

ownership-probe-check:
	/usr/bin/python3 -I -B scripts/validation_ownership/isolated_launcher.py

ownership-probe-test:
	python3 -m unittest scripts.validation_ownership.tests.test_foundation -v
