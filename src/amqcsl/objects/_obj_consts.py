ARTIST_TYPE = {
    0: 'Other',
    1: 'Person',
    2: 'Character',
    3: 'Group',
    4: 'Orchestra',
    5: 'Choir',
}
REVERSE_ARTIST_TYPE = {v: k for k, v in ARTIST_TYPE.items()}

TRACK_TYPE = {
    0: 'Vocal',
    1: 'OffVocal',
    2: 'Instrumental',
    3: 'Dialogue',
    10: 'Other',
}
REVERSE_TRACK_TYPE = {v: k for k, v in TRACK_TYPE.items()}

EXTRA_METADATA_TYPE = {
    1: 'Song',
    2: 'Artist',
}
REVERSE_EXTRA_METADATA_TYPE = {v: k for k, v in EXTRA_METADATA_TYPE.items()}

SONG_RELATION_TYPE = {
    0: 'Other',
    1: 'GroupMember',
    2: 'VoiceActor',
    3: 'Person',
}
REVERSE_SONG_RELATION_TYPE = {v: k for k, v in SONG_RELATION_TYPE.items()}

EMPTY_ID = '00000000-0000-0000-0000-000000000000'
