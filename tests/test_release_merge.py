"""Coverage for release features carried into the shared client implementation."""

import json

import pytest
from attrs import evolve
from helpers import finish, load
from httpx import HTTPStatusError, Response
from respx import Router

from amqcsl import AsyncDBClient, DBClient
from amqcsl.objects import CSLTrack, CSLTrackLink, CSLTrackRef

pytestmark = pytest.mark.asyncio


@pytest.mark.parametrize('queue', [False, True])
@pytest.mark.parametrize('status', [200, 500])
async def test_import_audio(
    db: DBClient | AsyncDBClient,
    router: Router,
    queue: bool,
    status: int,
) -> None:
    target = CSLTrack.from_json(load('sunshine/tracks')[0])
    source = evolve(target, id='source', audio_name='source.flac')
    route = router.post(f'/api/track/{target.id}/audio-import') % Response(status)
    before = router.calls.call_count
    if queue:
        await finish(db.import_audio(target, source, queue=True))
        assert router.calls.call_count == before
        assert len(db.queue) == 1

    async def operation() -> None:
        if queue:
            await finish(db.commit())
        else:
            await finish(db.import_audio(target, source))

    if status == 500:
        with pytest.raises(HTTPStatusError):
            await operation()
        assert len(db.queue) == int(queue)
    else:
        await operation()
        assert not db.queue
    assert route.call_count == 1
    assert json.loads(route.calls.last.request.content) == {
        'id': target.id,
        'url': 'https://amqbot.082640.xyz/files/source.flac',
    }
    assert router.calls.call_count == before + 1


async def test_failed_list_deletion_keeps_cached_lists(
    db: DBClient | AsyncDBClient,
    router: Router,
) -> None:
    lists = db.lists
    target = lists['MeiHayasaka']
    route = router.delete(f'/api/list/{target.id}') % Response(500)
    before = router.routes['lists'].call_count
    with pytest.raises(HTTPStatusError):
        await finish(db.list_delete(target))
    assert route.call_count == 1
    assert db.lists is lists
    assert router.routes['lists'].call_count == before


@pytest.mark.parametrize('kind', ['ref', 'link', 'simple', 'track'])
@pytest.mark.parametrize('container', ['list', 'tuple', 'generator'])
async def test_list_edit_accepts_track_references(
    db: DBClient | AsyncDBClient,
    router: Router,
    kind: str,
    container: str,
) -> None:
    target = db.lists['MeiHayasaka']
    track = CSLTrack.from_json(load('sunshine/tracks')[0])
    link = CSLTrackLink(track.id, track.name, track.artist_credits)
    references = {'ref': CSLTrackRef(track.id), 'link': link, 'simple': track.simp, 'track': track}
    reference = references[kind]
    assert link.ref() == CSLTrackRef(track.id)
    add = [reference] if container == 'list' else (reference,) if container == 'tuple' else iter([reference])
    remove = [reference] if container == 'list' else (reference,) if container == 'tuple' else iter([reference])
    route = router.put(f'/api/list/{target.id}') % Response(200)
    before = router.calls.call_count
    await finish(db.list_edit(target, add=add, remove=remove))
    assert route.call_count == 1
    payload = json.loads(route.calls.last.request.content)
    assert payload['addSongIds'] == payload['removeSongIds'] == [track.id]
    assert router.calls.call_count == before + 1
