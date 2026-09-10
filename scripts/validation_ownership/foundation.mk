.PHONY: ownership-probe-check ownership-probe-test

ownership-probe-check:
	/usr/bin/python3 -I -S -B scripts/validation_ownership/isolated_launcher.py

ownership-probe-test:
	python3 -m unittest scripts.validation_ownership.tests.test_foundation scripts.validation_ownership.tests.test_metadata_transport scripts.validation_ownership.tests.test_producer scripts.validation_ownership.tests.test_dependency -v
