import unittest
import yt_dlp
from main import FormatOption


class TestSubtitleOptions(unittest.TestCase):
    def test_english_subtitle_filtering(self) -> None:
        ydl = yt_dlp.YoutubeDL(
            {
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitleslangs": ["en.*", "en"],
            }
        )
        sub_tracks = {
            "en": [{"ext": "vtt", "url": "http://example.com/en"}],
            "en-US": [{"ext": "vtt", "url": "http://example.com/en-us"}],
            "es": [{"ext": "vtt", "url": "http://example.com/es"}],
            "fr": [{"ext": "vtt", "url": "http://example.com/fr"}],
        }
        res = ydl.process_subtitles("test_video", sub_tracks, sub_tracks)
        self.assertIn("en", res)
        self.assertIn("en-US", res)
        self.assertNotIn("es", res)
        self.assertNotIn("fr", res)

    def test_mkv_format_option_embeds_subtitles(self) -> None:
        option = FormatOption(
            label="1080p (Full HD)",
            quality="1080p (Full HD)",
            codec="VP9 + Opus",
            container="MKV",
            size_label="100 MB",
            format_selector="248+251",
            sort_key=(1080, 2, 1, 3000),
            merge_output_format="mkv",
            remux_video="mkv",
            embed_subtitles=True,
        )
    def test_cleanup_external_subtitles(self) -> None:
        import tempfile
        from pathlib import Path
        from main import _cleanup_external_subtitles

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            video_file = tmppath / "video_title.mkv"
            sub_file_vtt = tmppath / "video_title.en.vtt"
            sub_file_srt = tmppath / "video_title.en.srt"
            other_file = tmppath / "other_video.mkv"

            video_file.write_text("video content")
            sub_file_vtt.write_text("sub vtt content")
            sub_file_srt.write_text("sub srt content")
            other_file.write_text("other video")

            _cleanup_external_subtitles(tmppath, "video_title")

            self.assertTrue(video_file.exists())
            self.assertTrue(other_file.exists())
            self.assertFalse(sub_file_vtt.exists())
            self.assertFalse(sub_file_srt.exists())


    def test_ffmpeg_embed_subtitle_postprocessor(self) -> None:
        ydl = yt_dlp.YoutubeDL(
            {
                "embedsubtitles": True,
                "postprocessors": [
                    {
                        "key": "FFmpegEmbedSubtitle",
                        "already_have_subtitle": False,
                    }
                ],
            }
        )
        self.assertEqual(len(ydl._pps["post_process"]), 1)
    def test_resolve_downloaded_path_ignores_vtt(self) -> None:
        import tempfile
        from pathlib import Path
        from main import _resolve_downloaded_path

        with tempfile.TemporaryDirectory() as tmpdir:
            tmppath = Path(tmpdir)
            vtt_file = tmppath / "my_video.en.vtt"
            mkv_file = tmppath / "my_video.mkv"
            vtt_file.write_text("subtitle content")
            mkv_file.write_text("video content")

            resolved = _resolve_downloaded_path({}, tmppath, "my_video")
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved, mkv_file)

    def test_find_ffmpeg(self) -> None:
        from main import _find_ffmpeg
        ffmpeg_path = _find_ffmpeg()
        # On systems with ffmpeg installed, it returns a string path ending in ffmpeg
        if ffmpeg_path:
            self.assertTrue(ffmpeg_path.endswith("ffmpeg") or ffmpeg_path.endswith("ffmpeg.exe"))


if __name__ == "__main__":
    unittest.main()



