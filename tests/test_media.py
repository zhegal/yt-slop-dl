import pytest
from pathlib import Path

from app.downloader import build_arguments, build_inspection_arguments
from app.media import available_containers, format_duration, summarize_info


@pytest.mark.parametrize('video,audio,containers', [
    ('avc1.640028', 'mp4a.40.2', ['mkv', 'mp4']),
    ('av01.0.08M.08', 'opus', ['mkv', 'mp4', 'webm']),
    ('vp09.02.51', 'opus', ['mkv', 'mp4', 'webm']),
    ('vp8', 'vorbis', ['mkv', 'webm']),
    ('hevc', 'aac', ['mkv', 'mp4']),
    ('unknown', 'unknown', ['mkv']),
])
def test_container_compatibility(video, audio, containers):
    assert available_containers(video, audio) == containers


@pytest.mark.parametrize('value,expected', [(19,'0:19'),(61,'1:01'),(3661,'1:01:01'),(None,'—'),(float('inf'),'—')])
def test_duration(value, expected):
    assert format_duration(value) == expected


def test_summary_selected_streams():
    info = {'id':'abcdefghijk','title':'Тест','duration':19,'requested_formats':[
        {'format_id':'high','vcodec':'av01.0','acodec':'none','height':2160,'width':3840},
        {'format_id':'audio','vcodec':'none','acodec':'opus'},
    ], 'formats':[
        {'vcodec':'av01','height':2160}, {'vcodec':'avc1','height':720},
        {'vcodec':'images','height':90}, {'vcodec':'av01','height':4320,'has_drm':True},
    ]}
    summary = summarize_info(info)
    assert summary['format_id'] == 'high+audio'
    assert summary['height'] == 2160
    assert summary['resolutions'] == [720,2160]
    assert summary['containers'] == ['mkv','mp4','webm']
    assert summary['title'] == 'Тест'
    with pytest.raises(ValueError, match='Live'):
        summarize_info({**info,'is_live':True})
    with pytest.raises(ValueError, match='No downloadable'):
        summarize_info({'id':'abcdefghijk','vcodec':'none','acodec':'opus'})


def test_inspection_cannot_download_media(tmp_path):
    tools = {k:tmp_path/k for k in ['ffmpeg','deno']}
    args = build_inspection_arguments('https://youtu.be/abcdefghijk',tools)
    assert '--skip-download' in args and '--simulate' in args
    assert '--dump-single-json' in args
    assert '--no-simulate' not in args and '--paths' not in args


@pytest.mark.parametrize('container', ['mkv','mp4','webm'])
def test_explicit_container_preserves_streams(tmp_path, container):
    tools = {k:tmp_path/k for k in ['ffmpeg','deno']}
    args = build_arguments('https://youtu.be/abcdefghijk',tmp_path,tools,container,'high+audio')
    assert args[args.index('--format')+1] == 'high+audio'
    assert args[args.index('--merge-output-format')+1] == container
    assert args[args.index('--remux-video')+1] == container
    assert '--recode-video' not in args
    with pytest.raises(ValueError):
        build_arguments('https://youtu.be/abcdefghijk',tmp_path,tools,'bad-container')
    with pytest.raises(ValueError):
        build_arguments('https://youtu.be/abcdefghijk',tmp_path,tools,container,'best/bestvideo')


def selectable_info():
    return summarize_info({
        'id':'abcdefghijk','title':'Selectable',
        'formats':[
            {'format_id':'low','height':360,'fps':30,'vcodec':'avc1','acodec':'none'},
            {'format_id':'combined','height':480,'fps':30,'vcodec':'avc1','acodec':'mp4a'},
            {'format_id':'high','height':1080,'fps':60,'vcodec':'vp9','acodec':'none'},
            {'format_id':'audio-low','vcodec':'none','acodec':'mp4a','abr':64,'language':'en'},
            {'format_id':'audio-high','vcodec':'none','acodec':'opus','abr':160,'language':'en'},
            {'format_id':'other-language','vcodec':'none','acodec':'opus','abr':160,'language':'uk'},
        ],
        'requested_formats':[
            {'format_id':'high','height':1080,'fps':60,'vcodec':'vp9','acodec':'none'},
            {'format_id':'audio-high','vcodec':'none','acodec':'opus','abr':160,'language':'en'},
        ],
    })


def test_quality_choices_and_separate_streams():
    from app.media import select_streams
    info = selectable_info()
    assert [f['height'] for f in info['video_options']] == [1080,480,360]
    assert [f['format_id'] for f in info['audio_options']] == ['audio-high','audio-low']
    assert select_streams(info,'low')['format_id'] == 'low+audio-high'
    assert select_streams(info,'low',audio=False)['format_id'] == 'low'
    selected = select_streams(info,'audio-low',video=False)
    assert selected['format_id'] == 'audio-low' and selected['vcodec'] == 'none'
    assert 'm4a' in selected['containers'] and 'opus' not in selected['containers']
    assert 'webm' not in selected['containers']
    assert 'opus' in select_streams(info,'audio-high',video=False)['containers']
    with pytest.raises(ValueError,match='empty_selection'):
        select_streams(info,'low',False,False)
    with pytest.raises(ValueError,match='video_only_unavailable'):
        select_streams(info,'combined',True,False)
    assert select_streams(info,'combined')['format_id'] == 'combined'
    with pytest.raises(ValueError,match='unavailable_quality'):
        select_streams(info,'missing')


def test_audio_only_combined_fallback():
    from app.media import select_streams
    info = summarize_info({'id':'abcdefghijk','format_id':'combined','vcodec':'h264','acodec':'aac','height':360})
    assert select_streams(info,'combined',video=False)['format_id'] == 'combined'
    # yt-dlp's built-in audio extractor removes video in this fallback.
    tools = {k:Path('/tmp')/k for k in ['ffmpeg','deno']}
    args = build_arguments('https://youtu.be/abcdefghijk',Path('/tmp'),tools,
                           'm4a','combined',video=False)
    assert '--extract-audio' in args
    assert args[args.index('--audio-format')+1] == 'best'
    assert args[args.index('--remux-video')+1] == 'm4a'


@pytest.mark.parametrize('video,audio,selector',[(True,False,'bestvideo'),(False,True,'bestaudio/best')])
def test_single_stream_cli(tmp_path,video,audio,selector):
    tools = {k:tmp_path/k for k in ['ffmpeg','deno']}
    args = build_arguments('https://youtu.be/abcdefghijk',tmp_path,tools,video=video,audio=audio)
    assert args[args.index('--format')+1] == selector
    assert ('--extract-audio' in args) == (not video)
    assert '--recode-video' not in args
    with pytest.raises(ValueError,match='empty_selection'):
        build_arguments('https://youtu.be/abcdefghijk',tmp_path,tools,video=False,audio=False)
