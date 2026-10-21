"""Versioned, closed data models shared by every transport."""

from __future__ import annotations

from fractions import Fraction
from typing import Annotated, Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

Id = Annotated[str, Field(pattern=r"^[a-f0-9]{32}$")]
Hash = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
Sample = Annotated[int, Field(ge=0, strict=True)]


def new_id() -> str:
    return uuid4().hex


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, revalidate_instances="always")


class Provenance(Model):
    origin: Literal["model", "user", "ai_edit", "rule"]
    source_id: str = Field(min_length=1, max_length=256)
    review: Literal["unreviewed", "accepted", "corrected", "disputed"] = "unreviewed"
    confidence: float | None = Field(default=None, ge=0, le=1)
    algorithm: str | None = None


class Beat(Model):
    n: int
    d: int = Field(default=1, gt=0)

    @model_validator(mode="after")
    def reduce(self):
        f = Fraction(self.n, self.d)
        self.n, self.d = f.numerator, f.denominator
        return self

    def fraction(self) -> Fraction:
        return Fraction(self.n, self.d)

    @classmethod
    def of(cls, value):
        f = Fraction(value)
        return cls(n=f.numerator, d=f.denominator)


class TimingAnchor(Model):
    sample: Sample
    beat: Beat
    estimated: bool = False


class Meter(Model):
    beat: Beat
    bar: int = Field(ge=1)
    numerator: int = Field(ge=1, le=32)
    denominator: Literal[1, 2, 4, 8, 16, 32]


class TimingMap(Model):
    id: Id
    sample_rate: int = Field(ge=8000, le=192000)
    anchors: list[TimingAnchor] = Field(min_length=2)
    meters: list[Meter] = Field(min_length=1)
    pickup: Beat = Field(default_factory=lambda: Beat(n=0))
    provenance: Provenance

    @model_validator(mode="after")
    def monotonic(self):
        if self.pickup.fraction() < 0:
            raise ValueError("pickup must be nonnegative")
        if self.anchors[0].sample != 0 or self.anchors[0].beat.fraction() != -self.pickup.fraction():
            raise ValueError("sample zero must map to minus pickup")
        for a, b in zip(self.anchors, self.anchors[1:]):
            if a.sample >= b.sample or a.beat.fraction() >= b.beat.fraction():
                raise ValueError("timing anchors must increase strictly")
        if self.meters[0].beat.fraction() != 0 or self.meters[0].bar != 1:
            raise ValueError("first complete bar starts at beat zero, bar one")
        first = self.meters[0]
        if self.pickup.fraction() >= Fraction(first.numerator * 4, first.denominator):
            raise ValueError("pickup must be shorter than the first bar")
        for a, b in zip(self.meters, self.meters[1:]):
            bars = (b.beat.fraction() - a.beat.fraction()) / Fraction(a.numerator * 4, a.denominator)
            if bars.denominator != 1 or bars <= 0 or b.bar != a.bar + bars:
                raise ValueError("meter changes must occur on complete bar boundaries")
        return self


class Track(Model):
    id: Id
    name: str = Field(min_length=1, max_length=256)
    kind: Literal["audio", "instrument", "midi", "bus"]
    role: str | None = None
    instrument_candidates: list[str] = Field(default_factory=list)
    gain_db: float = Field(default=0, ge=-120, le=24)
    pan: float = Field(default=0, ge=-1, le=1)
    mute: bool = False
    solo: bool = False
    stem_asset_ids: list[Hash] = Field(default_factory=list)
    provenance: Provenance


class Clip(Model):
    id: Id
    track_id: Id
    asset_id: Hash | None = None
    start_sample: Sample
    duration_samples: int = Field(gt=0)
    source_offset_samples: Sample = 0
    loop: bool = False
    content_samples: int | None = Field(default=None, gt=0)
    gain_db: float = Field(default=0, ge=-120, le=24)
    provenance: Provenance


class NoteEvent(Model):
    id: Id
    track_id: Id
    clip_id: Id | None = None
    pitch: int = Field(ge=0, le=127)
    spelling: str | None = None
    start_sample: Sample
    end_sample: Sample
    velocity: float = Field(gt=0, le=1)
    channel: int = Field(default=1, ge=1, le=16)
    notation_beat: Beat | None = None
    notation_duration: Beat | None = None
    timing_mode: Literal["sample", "beat"] = "sample"
    timing_map_id: Id | None = None
    provenance: Provenance

    @model_validator(mode="after")
    def duration(self):
        if self.end_sample <= self.start_sample:
            raise ValueError("note duration must be positive")
        if (self.notation_beat is None) != (self.timing_map_id is None):
            raise ValueError("notation beat requires its specific timing map")
        if self.timing_mode == "beat" and (
            self.notation_beat is None
            or self.notation_duration is None
            or self.notation_duration.fraction() <= 0
        ):
            raise ValueError("beat-timed notes require a positive notation duration and mapped start")
        return self


class PluginInstance(Model):
    id: Id
    track_id: Id
    class_id: str
    version: str
    binary_sha256: Hash
    state_asset_id: Hash
    resource_asset_ids: list[Hash] = Field(default_factory=list)
    resource_paths: dict[str, Hash] = Field(default_factory=dict)
    resource_entrypoint: str | None = None
    descriptor_asset_id: Hash | None = None
    parameters: dict[str, float] = Field(default_factory=dict)
    bypass: bool = False
    order: int = Field(ge=0)
    preset_id: str | None = Field(default=None, max_length=200)
    preset_name: str | None = Field(default=None, max_length=256)

    @model_validator(mode="after")
    def normalized(self):
        if any(not 0 <= v <= 1 for v in self.parameters.values()):
            raise ValueError("plugin parameters must be normalized")
        return self


class RoutingEdge(Model):
    source: Id
    target: Id
    kind: Literal["main", "send", "sidechain"] = "main"
    gain_db: float = Field(default=0, ge=-120, le=24)


class RoutingGraph(Model):
    id: Id
    nodes: list[Id] = Field(default_factory=list)
    edges: list[RoutingEdge] = Field(default_factory=list)

    @model_validator(mode="after")
    def graph(self):
        if len(set(self.nodes)) != len(self.nodes):
            raise ValueError("duplicate graph nodes")
        adjacency = {n: [] for n in self.nodes}
        for edge in self.edges:
            if edge.source not in adjacency or edge.target not in adjacency:
                raise ValueError("unknown routing node")
            adjacency[edge.source].append(edge.target)
        done, active = set(), set()

        def visit(node):
            if node in active:
                raise ValueError("routing cycles are unsupported")
            if node in done:
                return
            active.add(node)
            for child in adjacency[node]:
                visit(child)
            active.remove(node)
            done.add(node)

        for node in adjacency:
            visit(node)
        return self


class AutomationPoint(Model):
    sample: Sample
    value: float = Field(ge=0, le=1)


class AutomationLane(Model):
    id: Id
    plugin_id: Id | None = None
    track_id: Id | None = None
    parameter_id: str
    interpolation: Literal["step", "linear"]
    points: list[AutomationPoint] = Field(min_length=1)
    provenance: Provenance

    @model_validator(mode="after")
    def ordered(self):
        if (self.plugin_id is None) == (self.track_id is None):
            raise ValueError("automation must target exactly one plugin or mixer track")
        if self.track_id and self.parameter_id not in ("gain", "pan"):
            raise ValueError("mixer automation supports normalized gain (-120..24 dB) or pan (-1..1)")
        if any(a.sample >= b.sample for a, b in zip(self.points, self.points[1:])):
            raise ValueError("automation points must increase strictly")
        return self


class Annotation(Model):
    id: Id
    start_sample: Sample
    end_sample: Sample
    kind: str = Field(min_length=1, max_length=80)
    text: str = Field(max_length=20000)
    provenance: Provenance
    details: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def interval(self):
        if self.end_sample <= self.start_sample:
            raise ValueError("annotation interval must be positive")
        return self


class Stem(Model):
    id: Id
    name: Literal["vocals", "drums", "bass", "other"]
    asset_id: Hash
    source_asset_id: Hash
    sample_rate: int
    duration_samples: Sample
    offset_samples: int = 0
    provenance: Provenance


class ControlEvent(Model):
    id: Id
    track_id: Id
    sample: Sample
    channel: int = Field(ge=1, le=16)
    controller: int = Field(ge=0, le=127)
    value: int = Field(ge=0, le=127)
    provenance: Provenance


class LoudnessMeasurement(Model):
    integrated_lufs: float = Field(ge=-120, le=20)
    true_peak_dbtp: float = Field(ge=-120, le=24)


class ListeningCalibration(Model):
    algorithm: Literal["source-ebu-r128-v1"]
    source_asset_id: Hash
    target_lufs: float = Field(ge=-120, le=20)
    mix_gain_db: float = Field(ge=-60, le=60)
    solo_gain_db: dict[Id, Annotated[float, Field(ge=-60, le=60)]]
    source: LoudnessMeasurement
    mix: LoudnessMeasurement
    tracks: dict[Id, LoudnessMeasurement]


class Snapshot(Model):
    listening_calibration: ListeningCalibration | None = None
    schema_version: Literal[1] = 1
    sample_rate: int = Field(default=44100, ge=8000, le=192000)
    duration_samples: Sample = 0
    source_asset_id: Hash | None = None
    analysis_run_ids: list[Id] = Field(default_factory=list)
    timing_map: TimingMap | None = None
    tracks: list[Track] = Field(default_factory=list)
    clips: list[Clip] = Field(default_factory=list)
    notes: list[NoteEvent] = Field(default_factory=list)
    plugins: list[PluginInstance] = Field(default_factory=list)
    routing: RoutingGraph | None = None
    automation: list[AutomationLane] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    stems: list[Stem] = Field(default_factory=list)
    controllers: list[ControlEvent] = Field(default_factory=list)
    track_order: list[Id] = Field(default_factory=list)
    frozen_tracks: dict[Id, Hash] = Field(default_factory=dict)

    @model_validator(mode="after")
    def references(self):
        if self.listening_calibration and self.listening_calibration.source_asset_id != self.source_asset_id:
            raise ValueError("Listening calibration belongs to a different source")
        collections = [
            self.tracks,
            self.clips,
            self.notes,
            self.plugins,
            self.automation,
            self.annotations,
            self.stems,
            self.controllers,
        ]
        ids = [e.id for group in collections for e in group]
        if len(ids) != len(set(ids)):
            raise ValueError("entity identifiers must be unique")
        tracks, plugins = {x.id for x in self.tracks}, {x.id: x for x in self.plugins}
        if self.track_order and (len(self.track_order) != len(tracks) or set(self.track_order) != tracks):
            raise ValueError("track order must contain each track once")
        if not set(self.frozen_tracks).issubset(tracks):
            raise ValueError("unknown frozen track")
        clips = {x.id: x for x in self.clips}
        for entity in [*self.clips, *self.notes, *self.plugins, *self.controllers]:
            if entity.track_id not in tracks:
                raise ValueError("unknown track")
        for note in self.notes:
            if note.clip_id and (note.clip_id not in clips or clips[note.clip_id].track_id != note.track_id):
                raise ValueError("unknown note clip")
            if note.clip_id:
                clip = clips[note.clip_id]
                if (
                    note.start_sample < clip.start_sample
                    or note.end_sample > clip.start_sample + clip.duration_samples
                ):
                    raise ValueError("owned note lies outside its clip")
            if note.end_sample > self.duration_samples:
                raise ValueError("note outside project duration")
            if note.timing_map_id and (not self.timing_map or note.timing_map_id != self.timing_map.id):
                raise ValueError("unknown notation timing map")
            if note.timing_mode == "beat":
                from .timing import convert

                begin = convert(self.sample_rate, self.timing_map, "beat", note.notation_beat, "sample")[
                    "rounded_sample"
                ]
                end = convert(
                    self.sample_rate,
                    self.timing_map,
                    "beat",
                    Beat.of(note.notation_beat.fraction() + note.notation_duration.fraction()),
                    "sample",
                )["rounded_sample"]
                if (begin, end) != (note.start_sample, note.end_sample):
                    raise ValueError("beat-timed note samples disagree with the bound timing map")
        for clip in self.clips:
            if clip.start_sample + clip.duration_samples > self.duration_samples:
                raise ValueError("clip outside project duration")
        for stem in self.stems:
            if (
                stem.source_asset_id != self.source_asset_id
                or stem.sample_rate != self.sample_rate
                or stem.duration_samples != self.duration_samples
                or stem.offset_samples != 0
            ):
                raise ValueError("stem does not share the original audio time base")
        for control in self.controllers:
            if control.sample >= self.duration_samples:
                raise ValueError("controller outside project duration")
        if self.timing_map:
            if self.timing_map.sample_rate != self.sample_rate:
                raise ValueError("timing map sample rate differs")
            if self.timing_map.anchors[-1].sample < self.duration_samples:
                raise ValueError("timing map does not cover the project")
        if len({(lane.plugin_id or lane.track_id, lane.parameter_id) for lane in self.automation}) != len(
            self.automation
        ):
            raise ValueError("only one automation lane per plugin parameter is allowed")
        for lane in self.automation:
            if lane.plugin_id and (
                lane.plugin_id not in plugins or lane.parameter_id not in plugins[lane.plugin_id].parameters
            ):
                raise ValueError("unknown automation parameter")
            if lane.track_id and lane.track_id not in tracks:
                raise ValueError("unknown mixer automation track")
            if lane.points[-1].sample > self.duration_samples:
                raise ValueError("automation outside project duration")
        return self


class Asset(Model):
    id: Hash
    bytes: int = Field(ge=0)
    media_type: str
    metadata: dict = Field(default_factory=dict)
    provenance: Provenance


class AnalysisRun(Model):
    id: Id
    input_asset_id: Hash
    adapter: str
    model_version: str
    weights_sha256: list[Hash]
    code_version: str
    configuration: dict
    raw_asset_ids: list[Hash]
    normalized_asset_ids: list[Hash]
    issues: list[str] = Field(default_factory=list)


class Project(Model):
    id: Id
    title: str
    kind: Literal["research", "composition"]
    status: Literal["empty", "imported", "queued", "processing", "ready", "failed", "cancelled"]
    current_revision: Id
    created_at: float
    archived: bool = False
    display_title: str | None = None


class Revision(Model):
    id: Id
    project_id: Id
    parent_id: Id | None
    snapshot_sha256: Hash
    snapshot: Snapshot
    author: str
    description: str
    created_at: float


class Job(Model):
    id: Id
    project_id: Id
    revision_id: Id
    kind: Literal[
        "asset.import",
        "plugin.inspect",
        "plugin.scan",
        "project.preprocess",
        "project.render",
        "audio.measure",
        "project.export",
        "application.backup",
        "application.restore",
        "application.diagnose",
        "instrument.capture",
    ]
    status: Literal["queued", "running", "cancel_requested", "cancelled", "succeeded", "failed"]
    payload: dict
    attempt: int
    max_attempts: int
    lease_until: float | None = None
    result: dict | None = None
    error: dict | None = None
    created_at: float
    updated_at: float


class Conversation(Model):
    id: Id
    project_id: Id
    revision_id: Id
    role: Literal["user", "assistant", "tool", "note"]
    content: str = Field(min_length=1, max_length=100000)
    context: dict = Field(default_factory=dict)
    created_at: float


class Request(Model):
    schema_version: Literal[1] = 1
    request_id: Id = Field(default_factory=new_id)
    operation: str
    arguments: dict = Field(default_factory=dict)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    expected_revision: Id | None = None


class Empty(Model):
    pass


class Page(Model):
    offset: int = Field(default=0, ge=0)
    limit: int = Field(default=100, ge=1, le=1000)


class CreateProject(Model):
    title: str = Field(min_length=1, max_length=256)
    kind: Literal["research", "composition"] = "research"


class ProjectRef(Model):
    project_id: Id


class ProjectDeletion(ProjectRef):
    revision_id: Id
    title: str
    status: Literal["waiting", "purging", "failed", "completed"]
    deleted: bool
    removed_rows: dict[str, int]
    removed_assets: int
    removed_bytes: int
    retained_shared: int
    error: str | None = None


class ProjectList(Page):
    include_archived: bool = False


class OrganizeProject(ProjectRef):
    archived: bool = False
    display_title: str | None = Field(default=None, min_length=1, max_length=256)


class RevisionRef(ProjectRef):
    revision_id: Id | None = None


class ProjectPage(ProjectRef, Page):
    pass


class RenameProject(ProjectRef):
    title: str = Field(min_length=1, max_length=256)


class ForkProject(RevisionRef):
    title: str = Field(min_length=1, max_length=256)


class CommitSnapshot(ProjectRef):
    snapshot: Snapshot
    author: str = Field(min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)


class RegisterAnalysis(ProjectRef):
    run: AnalysisRun


class AppendConversation(ProjectRef):
    revision_id: Id
    role: Literal["user", "assistant", "tool", "note"]
    content: str = Field(min_length=1, max_length=100000)
    context: dict = Field(default_factory=dict)


class QueryEvents(RevisionRef, Page):
    track_id: Id | None = None
    start_sample: Sample = 0
    end_sample: Sample | None = None

    @model_validator(mode="after")
    def interval(self):
        if self.end_sample is not None and self.end_sample <= self.start_sample:
            raise ValueError("query interval must be nonempty")
        return self


class ConvertTime(RevisionRef):
    from_unit: Literal["sample", "seconds", "beat"]
    value: Beat
    to_unit: Literal["sample", "seconds", "beat", "bar"]


class SubmitJob(ProjectRef):
    kind: Literal[
        "asset.import",
        "plugin.inspect",
        "plugin.scan",
        "project.preprocess",
        "project.render",
        "audio.measure",
        "project.export",
        "application.backup",
        "application.restore",
        "application.diagnose",
        "instrument.capture",
    ]
    payload: dict
    max_attempts: int = Field(default=3, ge=1, le=5)


class ImportPayload(Model):
    upload_id: Id
    preprocess: bool = False


class BackupPayload(Model):
    destination: str = Field(min_length=1, max_length=2000)


class DiagnosePayload(Model):
    use_existing_login: bool = False


class RestorePayload(BackupPayload):
    archive: str = Field(min_length=1, max_length=2000)
    expected_sha256: Hash | None = None
    port: int = Field(default=8766, ge=1024, le=65535)


class PreprocessPayload(Model):
    use_cache: bool = True


class AdoptPreprocessing(ProjectRef):
    revision_id: Id


class StageRecord(Model):
    job_id: Id
    stage: str
    ordinal: int
    status: Literal["pending", "running", "succeeded", "failed", "cancelled"]
    cache_key: Hash | None = None
    cache_hit: bool = False
    progress: dict = Field(default_factory=dict)
    manifest: dict | None = None
    error: dict | None = None
    updated_at: float


class PreprocessingResult(Model):
    tail_samples: Sample | None = None
    project_id: Id
    revision_id: Id
    job_id: Id
    manifest_asset_id: Hash
    report_asset_id: Hash
    mix_asset_id: Hash
    native_snapshot_asset_id: Hash
    track_renders: dict[Id, Hash]
    waveforms: dict[str, Hash]
    issues: list[str]
    created_at: float


class PreprocessingView(Model):
    revision_id: Id
    ready: bool
    publication: PreprocessingResult | None


class PluginPayload(Model):
    path: str = Field(min_length=1, max_length=4096)
    identifier: str | None = None


class CaptureInstrument(Model):
    preset_id: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9_.-]+$")


class InstrumentModelSpec(Model):
    key: str = Field(min_length=1, max_length=256, pattern=r".*\S.*")
    name: str = Field(min_length=1, max_length=256, pattern=r".*\S.*")
    kind: Literal["instrument", "layer", "synth", "unspecified"] = "instrument"


class InstrumentQuery(Page):
    search: str = Field(default="", max_length=200)
    family: str | None = Field(default=None, max_length=80)
    instrument_id: str | None = Field(default=None, min_length=1, max_length=80)
    audio_verified: bool | None = None
    model_id: str | None = Field(default=None, min_length=1, max_length=80)


class InstrumentModelQuery(InstrumentQuery):
    current_preset_id: str | None = Field(default=None, min_length=1, max_length=200)


class InstrumentModelItem(Model):
    id: str
    name: str
    display_name: str
    kind: Literal["instrument", "layer", "synth", "unspecified"]
    basis: str
    model_spec: InstrumentModelSpec
    source: dict[str, str]
    instrument_ids: list[str]
    variant_count: int
    preview_preset_id: str | None


class InstrumentLabels(Model):
    preset_ids: list[str] = Field(default_factory=list, max_length=128)
    parameter_names: list[str] = Field(default_factory=list, max_length=256)


class PrepareInstrumentPreview(Model):
    preset_id: str = Field(min_length=1, max_length=200)
    retry: bool = False


class InstrumentPreviewRef(Model):
    preview_id: Hash


class InstrumentPreview(Model):
    id: Hash
    preset_id: str
    display_name: str
    status: Literal["queued", "loading", "ready", "failed", "cancelled"]
    audio_url: str | None = None
    error: str | None = None
    duration: float | None = None
    phrase: str
    cached: bool = False


class InstrumentModels(Model):
    total: int
    items: list[InstrumentModelItem]
    next_offset: int | None
    current_model_id: str | None


class InstrumentCategory(Model):
    id: str
    name: str
    parent_id: str | None
    aliases: list[str]
    preset_count: int
    verified_count: int
    library_count: int


class InstrumentCategories(Model):
    taxonomy_version: int
    total_presets: int
    items: list[InstrumentCategory]


class RecommendInstruments(RevisionRef):
    track_id: Id | None = None


class JobRef(Model):
    job_id: Id


class AssetRef(Model):
    asset_id: Hash


class RegisterAsset(ProjectRef):
    upload_id: Id
    provenance: Provenance


class EventPage(Page):
    after: int = Field(default=0, ge=0)
    project_id: Id | None = None


class EventRecord(Model):
    cursor: int
    type: str
    project_id: Id | None
    job_id: Id | None
    revision_id: Id | None
    request_id: Id | None
    payload: dict
    created_at: float


class AnalysisRecord(Model):
    id: Id
    project_id: Id
    payload: AnalysisRun
    created_at: float


class QueryNotesResult(Model):
    revision_id: Id
    total: int
    items: list[NoteEvent]


class QueryAnnotationsResult(Model):
    revision_id: Id
    total: int
    items: list[Annotation]


class UploadResult(Model):
    upload_id: Id
    sha256: Hash
    bytes: int


class EditScope(Model):
    track_ids: list[Id] = Field(default_factory=list)
    start_sample: Sample = 0
    end_sample: Sample
    structural: bool = False

    @model_validator(mode="after")
    def interval(self):
        if self.end_sample <= self.start_sample:
            raise ValueError("edit scope must be nonempty")
        return self


class PutEntity(Model):
    op: Literal["put"]
    collection: Literal["tracks", "clips", "notes", "controllers", "plugins", "automation", "annotations"]
    entity: Track | Clip | NoteEvent | ControlEvent | PluginInstance | AutomationLane | Annotation


class DeleteEntity(Model):
    op: Literal["delete"]
    collection: Literal["tracks", "clips", "notes", "controllers", "plugins", "automation", "annotations"]
    id: Id


class SetTiming(Model):
    op: Literal["timing"]
    timing_map: TimingMap


class SetRouting(Model):
    op: Literal["routing"]
    routing: RoutingGraph


class SetTrackOrder(Model):
    op: Literal["track_order"]
    track_ids: list[Id]


class ArrangeClip(Model):
    op: Literal["arrange_clip"]
    id: Id
    action: Literal["move", "duplicate", "split", "trim"]
    sample: Sample
    end_sample: Sample | None = None
    new_id: Id | None = None


class FreezeTrack(Model):
    op: Literal["freeze"]
    track_id: Id
    render_job_id: Id | None = None


class PatchPlugin(Model):
    op: Literal["patch_plugin"]
    id: Id
    parameters: dict[str, float] = Field(default_factory=dict)
    bypass: bool | None = None
    order: int | None = Field(default=None, ge=0)
    preset_plugin_id: Id | None = None
    preset_capture_job_id: Id | None = None


class PatchNote(Model):
    op: Literal["patch_note"]
    id: Id
    pitch: int | None = Field(default=None, ge=0, le=127)
    velocity: float | None = Field(default=None, gt=0, le=1)
    provenance: Provenance | None = None


EditOperation = Annotated[
    PutEntity
    | DeleteEntity
    | SetTiming
    | SetRouting
    | SetTrackOrder
    | ArrangeClip
    | FreezeTrack
    | PatchPlugin
    | PatchNote,
    Field(discriminator="op"),
]


class ApplyEdit(ProjectRef):
    scope: EditScope
    operations: list[EditOperation] = Field(min_length=1, max_length=10000)
    author: str = Field(default="agent", min_length=1, max_length=128)
    description: str = Field(min_length=1, max_length=2000)


class CompareRevisions(ProjectRef):
    before_revision: Id
    after_revision: Id


class RestoreRevision(ProjectRef):
    revision_id: Id
    description: str = Field(default="Restore revision", min_length=1, max_length=2000)


class RenderPayload(Model):
    revision_id: Id | None = None
    preview: bool = False
    tail_seconds: float = Field(default=2, ge=0, le=60)
    block_size: int = Field(default=512, ge=16, le=8192)
    start_sample: Sample = 0
    end_sample: Sample | None = None


class MeasurePayload(Model):
    asset_id: Hash
    start_sample: Sample = 0
    end_sample: Sample | None = None


class ExportPayload(Model):
    revision_id: Id | None = None
    format: Literal["midi", "wav"]
    render_job_id: Id | None = None
    mixing_version: int | None = Field(default=None, ge=0, strict=True)


class AuditionState(Model):
    revision_id: Id
    level_match: bool = True
    position_sample: Sample = 0
    mode: Literal["original", "mix", "tracks", "reference"] = "original"
    reference_revision_id: Id | None = None
    track_ids: list[Id] = Field(default_factory=list)
    loop_start_sample: Sample = 0
    loop_end_sample: Sample | None = None
    loop: bool = False
    playing: bool = False


class SetAudition(ProjectRef):
    state: AuditionState


class MixerPatch(Model):
    track_id: Id
    gain_db: float | None = Field(default=None, ge=-120, le=24)
    pan: float | None = Field(default=None, ge=-1, le=1)
    mute: bool | None = None
    solo: bool | None = None
    name: str | None = Field(default=None, min_length=1, max_length=256)


class SetMixer(ProjectRef):
    tracks: list[MixerPatch] = Field(min_length=1, max_length=256)
    description: str = Field(default="调整音轨混音", min_length=1, max_length=2000)


class MixValues(Model):
    gain_db: float = Field(default=0, ge=-120, le=24)
    pan: float = Field(default=0, ge=-1, le=1)
    mute: bool = False
    solo: bool = False


class SaveMixing(ProjectRef):
    expected_version: int = Field(ge=0, strict=True)
    tracks: dict[Id, MixValues] = Field(max_length=256)


class MixingState(Model):
    version: int
    tracks: dict[Id, MixValues]


class PlaybackStem(Model):
    asset_id: Hash
    source_revision_id: Id
    baked_gain_db: float
    baked_pan: float
    mix_asset_id: Hash
    tail_samples: Sample


class PlaybackPlan(Model):
    revision_id: Id
    available: bool
    tracks: dict[str, PlaybackStem]
    pending_track_ids: list[Id]
    reason: str | None = None


class MixerResult(Model):
    revision: Revision
    playback: PlaybackPlan
    preparation_job_id: Id | None = None


class ReportAudition(ProjectRef):
    sequence: Id
    position_sample: Sample
    status: Literal["playing", "paused", "loading", "needs_activation", "error"]


class MusicQuery(QueryEvents):
    action: Literal["summary", "events", "annotations", "compare", "metrics"] = "summary"
    compare_start_sample: Sample | None = None
    compare_end_sample: Sample | None = None
    limit: int = Field(default=40, ge=1, le=100)
    annotation_kind: str | None = Field(default=None, min_length=1, max_length=80)


class DialogueSession(Model):
    id: Id
    project_id: Id
    title: str
    created_at: float


class AvailableModel(Model):
    id: str
    name: str
    is_default: bool


class AvailableModels(Model):
    models: list[AvailableModel]
    login_mode: Literal["isolated", "existing"]


class CreateDialogueSession(ProjectRef):
    title: str = Field(default="新会话", min_length=1, max_length=120)


class DialogueList(ProjectPage):
    session_id: Id | None = None


class ModelCatalog(Model):
    refresh: bool = False
    login_mode: Literal["isolated", "existing"] = "isolated"


class SubmitDialogue(RevisionRef):
    edit_mode: Literal["auto", "read_only"] = "auto"
    session_id: Id | None = None
    model: str | None = Field(default=None, min_length=1, max_length=200)
    text: str = Field(min_length=1, max_length=12000)
    start_sample: Sample = 0
    end_sample: Sample | None = None
    track_id: Id | None = None
    login_mode: Literal["isolated", "existing"] = "isolated"
    edit_scope: EditScope | None = None


class DialogueRef(ProjectRef):
    turn_id: Id


class AgentEdit(DialogueRef):
    operations: list[EditOperation] = Field(min_length=1, max_length=1000)
    description: str = Field(min_length=1, max_length=2000)


class ProductionQuery(RevisionRef, Page):
    track_id: Id | None = None
    plugin_id: Id | None = None
    parameter_search: str = Field(default="", max_length=200)
    preset_search: str | None = Field(default=None, max_length=200)
    instrument_id: str | None = Field(default=None, min_length=1, max_length=80)
    include_instrument_categories: bool = False
    model_id: str | None = Field(default=None, min_length=1, max_length=80)
    include_instrument_models: bool = False
    capture_job_id: Id | None = None
    limit: int = Field(default=40, ge=1, le=100)


class SaveNote(RevisionRef):
    content: str = Field(min_length=1, max_length=12000)
    start_sample: Sample = 0
    end_sample: Sample | None = None
    track_id: Id | None = None
    source_turn_id: Id | None = None
    state: Literal["draft", "accepted", "disputed"] = "draft"


class Citation(Model):
    id: Id
    project_id: Id
    revision_id: Id
    start_sample: Sample
    end_sample: Sample
    track_id: Id | None = None
    label: str
    entity_ids: list[Id] = Field(default_factory=list)
    source: Literal["project_events_and_candidates", "render_measurement"]


class DialogueTurn(Model):
    session_id: Id | None = None
    id: Id
    project_id: Id
    revision_id: Id
    status: Literal["queued", "running", "cancel_requested", "cancelled", "succeeded", "failed"]
    payload: dict
    context: dict
    text: str
    tools: list[dict]
    citations: list[Citation]
    error: dict | None
    created_at: float
    updated_at: float


class ApplicationPreferences(Model):
    default_model: str | None = Field(default=None, min_length=1, max_length=128)
    login_mode: Literal["isolated", "existing"] = "isolated"
    level_match: bool = True


class PreferencesPatch(Model):
    default_model: str | None = Field(default=None, min_length=1, max_length=128)
    login_mode: Literal["isolated", "existing"] | None = None
    level_match: bool | None = None


class UpdateApplicationSettings(Model):
    expected_version: int = Field(ge=0)
    preferences: PreferencesPatch


class TrackLevel(Model):
    gain_db: float = Field(ge=-120, le=24)


class SaveVersion(RevisionRef):
    title: str = Field(default="保存版本", min_length=1, max_length=200)
    track_levels: dict[Id, TrackLevel] | None = None


class LiveOpen(RevisionRef):
    track_id: Id
    client_id: Id
    client_sequence: int = Field(default=0, ge=0)


class LiveSession(Model):
    session_id: Id


class LiveChunk(LiveSession):
    generation: int = Field(ge=1)
    sample: int = Field(ge=0)
    frames: int = Field(default=8192, ge=1, le=16384)


OPERATIONS = {
    "storage.plan": (ProjectRef, False),
    "storage.collect": (ProjectRef, True),
    "storage.status": (ProjectRef, False),
    "versions.list": (ProjectPage, False),
    "versions.save": (SaveVersion, True),
    "playback.live.open": (LiveOpen, False),
    "playback.live.status": (LiveSession, False),
    "playback.live.chunk": (LiveChunk, False),
    "playback.live.close": (LiveSession, False),
    "describe_capabilities": (Empty, False),
    "application.settings.get": (Empty, False),
    "application.settings.set": (UpdateApplicationSettings, True),
    "projects.list": (ProjectList, False),
    "projects.organize": (OrganizeProject, True),
    "projects.delete": (ProjectRef, True),
    "projects.deletions.list": (Page, False),
    "projects.create": (CreateProject, True),
    "projects.get": (ProjectRef, False),
    "projects.rename": (RenameProject, True),
    "projects.fork": (ForkProject, True),
    "revisions.list": (ProjectPage, False),
    "revisions.get": (RevisionRef, False),
    "revisions.commit": (CommitSnapshot, True),
    "events.query": (QueryEvents, False),
    "annotations.query": (QueryEvents, False),
    "controllers.query": (QueryEvents, False),
    "time.convert": (ConvertTime, False),
    "assets.list": (ProjectPage, False),
    "assets.get": (AssetRef, False),
    "assets.register": (RegisterAsset, True),
    "analysis.register": (RegisterAnalysis, True),
    "analysis.list": (ProjectPage, False),
    "conversations.append": (AppendConversation, True),
    "conversations.list": (ProjectPage, False),
    "jobs.submit": (SubmitJob, True),
    "jobs.get": (JobRef, False),
    "jobs.list": (ProjectPage, False),
    "jobs.cancel": (JobRef, True),
    "jobs.retry": (JobRef, True),
    "jobs.stages": (JobRef, False),
    "preprocessing.get": (RevisionRef, False),
    "preprocessing.adopt": (AdoptPreprocessing, True),
    "events.list": (EventPage, False),
    "edits.apply": (ApplyEdit, True),
    "revisions.compare": (CompareRevisions, False),
    "revisions.restore": (RestoreRevision, True),
    "graph.get": (RevisionRef, False),
    "renders.list": (RevisionRef, False),
    "plugins.presets": (RevisionRef, False),
    "audition.get": (ProjectRef, False),
    "playback.get": (RevisionRef, False),
    "mixer.set": (SetMixer, True),
    "mixing.get": (ProjectRef, False),
    "mixing.save": (SaveMixing, True),
    "audition.set": (SetAudition, True),
    "audition.report": (ReportAudition, True),
    "music.query": (MusicQuery, False),
    "dialogue.submit": (SubmitDialogue, True),
    "dialogue.get": (DialogueRef, False),
    "dialogue.list": (DialogueList, False),
    "dialogue.sessions.list": (ProjectPage, False),
    "dialogue.sessions.create": (CreateDialogueSession, True),
    "dialogue.models": (ModelCatalog, False),
    "dialogue.cancel": (DialogueRef, True),
    "dialogue.retry": (DialogueRef, True),
    "notes.save": (SaveNote, True),
    "arrangements.apply": (AgentEdit, True),
    "arrangements.get": (DialogueRef, False),
    "production.query": (ProductionQuery, False),
    "instruments.list": (InstrumentQuery, False),
    "instruments.models": (InstrumentModelQuery, False),
    "instruments.labels": (InstrumentLabels, False),
    "instruments.preview.prepare": (PrepareInstrumentPreview, True),
    "instruments.preview.get": (InstrumentPreviewRef, False),
    "instruments.preview.cancel": (InstrumentPreviewRef, True),
    "instruments.preview.stats": (Empty, False),
    "instruments.categories": (Empty, False),
    "instruments.recommend": (RecommendInstruments, False),
}

RESULT_TYPES = {
    "storage.plan": dict,
    "storage.collect": dict,
    "storage.status": dict,
    "versions.list": list[dict],
    "versions.save": dict,
    "playback.live.open": dict,
    "playback.live.status": dict,
    "playback.live.chunk": dict,
    "playback.live.close": dict,
    "describe_capabilities": dict,
    "application.settings.get": dict,
    "application.settings.set": dict,
    "projects.list": list[Project],
    "projects.organize": Project,
    "projects.delete": ProjectDeletion,
    "projects.deletions.list": list[ProjectDeletion],
    "projects.create": Project,
    "projects.get": Project,
    "projects.rename": Project,
    "projects.fork": Project,
    "revisions.list": list[dict],
    "revisions.get": Revision,
    "revisions.commit": Revision,
    "events.query": QueryNotesResult,
    "annotations.query": QueryAnnotationsResult,
    "controllers.query": dict,
    "time.convert": dict,
    "assets.list": list[Asset],
    "assets.get": Asset,
    "assets.register": Asset,
    "analysis.register": AnalysisRun,
    "analysis.list": list[AnalysisRecord],
    "conversations.append": Conversation,
    "conversations.list": list[Conversation],
    "jobs.submit": Job,
    "jobs.get": Job,
    "jobs.list": list[Job],
    "jobs.cancel": Job,
    "jobs.retry": Job,
    "jobs.stages": list[StageRecord],
    "preprocessing.get": PreprocessingView,
    "preprocessing.adopt": Project,
    "events.list": list[EventRecord],
    "edits.apply": Revision,
    "revisions.compare": dict,
    "revisions.restore": Revision,
    "graph.get": dict,
    "renders.list": list[dict],
    "plugins.presets": list[dict],
    "instruments.list": dict,
    "instruments.models": InstrumentModels,
    "instruments.labels": dict,
    "instruments.preview.prepare": InstrumentPreview,
    "instruments.preview.get": InstrumentPreview,
    "instruments.preview.cancel": InstrumentPreview,
    "instruments.preview.stats": dict,
    "instruments.categories": InstrumentCategories,
    "instruments.recommend": dict,
    "audition.get": dict,
    "playback.get": PlaybackPlan,
    "mixer.set": MixerResult,
    "mixing.get": MixingState,
    "mixing.save": MixingState,
    "audition.set": dict,
    "audition.report": dict,
    "music.query": dict,
    "dialogue.submit": DialogueTurn,
    "dialogue.get": DialogueTurn,
    "dialogue.list": list[DialogueTurn],
    "dialogue.sessions.list": list[DialogueSession],
    "dialogue.sessions.create": DialogueSession,
    "dialogue.models": AvailableModels,
    "dialogue.cancel": DialogueTurn,
    "dialogue.retry": DialogueTurn,
    "notes.save": Conversation,
    "arrangements.apply": dict,
    "arrangements.get": dict,
    "production.query": dict,
}
