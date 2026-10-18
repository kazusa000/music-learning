import hashlib
import json
import mido
import pytest

from make_whole_song import compose
from midi_to_native import normalize, to_project, load_drum_map
from model_runtime import verify_file


def save(tmp_path, messages):
    path=tmp_path/"test.mid"
    midi=mido.MidiFile(ticks_per_beat=480);midi.tracks.append(mido.MidiTrack(messages));midi.save(path)
    return path


def test_note_spanning_tempo_change_keeps_sample_time(tmp_path):
    path=save(tmp_path,[mido.Message("note_on",note=60,velocity=64),
        mido.MetaMessage("set_tempo",tempo=1000000,time=480),
        mido.Message("note_off",note=60,time=480)])
    result=normalize(path)
    note=result["tracks"][0]["notes"][0]
    assert note["sample"]==0 and note["duration_samples"]==72000
    assert result["tempo"][1]=={"sample":24000,"bpm":60}


def test_unclosed_note_is_not_given_an_invented_end(tmp_path):
    path=save(tmp_path,[mido.Message("note_on",note=60,velocity=64)])
    with pytest.raises(ValueError,match="Unclosed"):normalize(path)


def test_conflicting_tempos_at_zero_are_not_silently_overwritten(tmp_path):
    path=save(tmp_path,[mido.MetaMessage("set_tempo",tempo=500000),
                        mido.MetaMessage("set_tempo",tempo=600000)])
    with pytest.raises(ValueError,match="Conflicting"):normalize(path)


def test_overlapping_same_pitch_is_visible_and_reconstruction_rejects(tmp_path):
    path=save(tmp_path,[mido.Message("note_on",note=60,velocity=64),
        mido.Message("note_on",note=60,velocity=64,time=120),
        mido.Message("note_off",note=60,time=120),mido.Message("note_off",note=60,time=120)])
    result=normalize(path)
    assert result["issues"][0]["type"]=="overlapping_same_pitch"
    with pytest.raises(ValueError,match="Ambiguous"):to_project(result,48000)


def test_pitchbend_is_preserved_and_not_silently_dropped(tmp_path):
    path=save(tmp_path,[mido.Message("note_on",note=60,velocity=64),
        mido.Message("pitchwheel",pitch=300,time=120),mido.Message("note_off",note=60,time=120)])
    result=normalize(path)
    assert result["unhandled_events"][0]["event"]["pitch"]==300
    with pytest.raises(ValueError,match="Unsupported"):to_project(result,48000)


def test_model_file_digests_reject_changed_content(tmp_path):
    path=tmp_path/"model";path.write_bytes(b"model")
    blob=hashlib.sha1(b"blob 5\0model").hexdigest()
    assert verify_file(path,{"size":5,"sha256":None,"git_blob":blob})==hashlib.sha256(b"model").hexdigest()
    path.write_bytes(b"wrong")
    with pytest.raises(ValueError,match="blob"):verify_file(path,{"size":5,"sha256":None,"git_blob":blob})


def test_fixture_chord_truth_matches_played_harmony():
    project,truth=compose({"bell":"unused.sfz","drums":"unused.sfz"})
    roots={"C":0,"A":9,"F":5,"G":7,"E":4}
    qualities={"maj7":{0,4,7,11},"min7":{0,3,7,10},"maj":{0,4,7},"7":{0,4,7,10}}
    notes=project["tracks"][0]["notes"]
    for chord in truth["chords"]:
        root,quality=chord["chord"].split(":")
        played={n["note"]%12 for n in notes if n["sample"]==chord["start_sample"]}
        expected={(roots[root]+interval)%12 for interval in qualities[quality]}
        assert played==expected


def drum_manifest(tmp_path, rms=.01):
    sfz=tmp_path/"kit.sfz";sfz.write_text('<region> sample=owned.wav key=36')
    data={"schema_version":1,"sfz_file":"kit.sfz","supported_notes":[36],
          "assets":[{"path":"kit.sfz","sha256":hashlib.sha256(sfz.read_bytes()).hexdigest()}],
          "per_key_render_rms":{"36":rms},"plugin_binary_sha256":"0"*64,"description":"diagnostic"}
    path=tmp_path/"drum-map.json";path.write_text(json.dumps(data));return path


def test_drum_map_rejects_a_modified_verified_dependency(tmp_path):
    manifest=drum_manifest(tmp_path)
    assert load_drum_map(manifest)["supported_notes"]==[36]
    (tmp_path/"kit.sfz").write_text('changed')
    with pytest.raises(ValueError,match="digest"):load_drum_map(manifest)


def test_nan_cannot_count_as_a_successful_per_key_audio_measurement(tmp_path):
    with pytest.raises(ValueError,match="evidence"):load_drum_map(drum_manifest(tmp_path,float('nan')))
