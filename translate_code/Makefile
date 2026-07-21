.PHONY: demo test sample

sample:
	@mkdir -p samples
	@test -f samples/sample_ad.mp4 || ffmpeg -y -f lavfi -i "testsrc=duration=4:size=640x360:rate=25" \
		-f lavfi -i "sine=frequency=440:duration=4" \
		-c:v libx264 -pix_fmt yuv420p -c:a aac -shortest samples/sample_ad.mp4

demo: sample
	uv run ad-localizer run samples/sample_ad.mp4 --to es --dry-run --out ./work

test:
	uv run pytest tests/ -q
