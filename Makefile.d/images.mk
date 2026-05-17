# Base image targets - included by top-level Makefile.

.PHONY: images images-common images-data images-infra images-clean

images: images-common images-data images-infra

images-common:
	./bin/molecule_images.sh common

images-data: images-common
	./bin/molecule_images.sh data

images-infra: images-common
	./bin/molecule_images.sh infra

images-clean:
	-podman image rm -f localhost/molecule-base-common:latest \
	                    localhost/molecule-base-data:latest \
	                    localhost/molecule-base-infra:latest
