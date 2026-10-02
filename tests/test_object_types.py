from amqcsl.objects import CSLArtist, CSLArtistSample, CSLExtraMetadata, CSLSongRelation


def test_artist_to_sample_strips_relations():
    full = CSLArtist('id', 'Name', 'Original', 'Disambiguation', 3, [], [], [], [])
    sample = full.to_sample()
    assert type(sample) is CSLArtistSample
    assert sample == CSLArtistSample('id', 'Name', 'Original', 'Disambiguation', 3)
    assert hash(sample) == hash(sample.to_sample())
    assert full.type == 'Group'


def test_numeric_types_keep_their_existing_values():
    sample = CSLArtistSample('id', 'Name', '', None, 1)
    assert sample.type == 'Person'
    assert CSLExtraMetadata('id', 2, 'Character', 'Name').type == 'Artist'
    assert CSLSongRelation('id', 1, sample).type == 'GroupMember'
