package main

import (
	"context"
	"encoding/binary"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"net"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"sort"
	"strings"
	"sync"
	"syscall"
	"time"

	lksdk "github.com/livekit/server-sdk-go/v2"
	"github.com/livekit/server-sdk-go/v2/pkg/samplebuilder"
	"github.com/livekit/protocol/livekit"
	"github.com/pion/rtcp"
	"github.com/pion/rtp"
	"github.com/pion/rtp/codecs"
	"github.com/pion/webrtc/v4"
	"github.com/pion/webrtc/v4/pkg/media/h264writer"
)

const udpStartupGracePeriod = 15 * time.Second
const videoPLIBurstInterval = 200 * time.Millisecond
const videoPLIBurstDuration = 20 * time.Second
const videoPLISteadyInterval = 1 * time.Second
const localUDPBufferBytes = 4 * 1024 * 1024
const defaultArchiveOutputWidth = 960
const defaultArchiveOutputHeight = 540
const defaultArchiveVideoBitrateKbps = 5000
const defaultHLSOutputWidth = 960
const defaultHLSOutputHeight = 540
const defaultHLSVideoBitrateKbps = 3500
const archiveSampleMaxLate = 5000
const effectiveFPSWindow = 8 * time.Second
const lowFrameRateThreshold = 10.0

type appStatus struct {
	StartedAt       string   `json:"startedAt"`
	UpdatedAt       string   `json:"updatedAt"`
	State           string   `json:"state"`
	ServerHost      string   `json:"serverHost"`
	RoomName        string   `json:"roomName"`
	Connected       bool     `json:"connected"`
	VideoCodec      string   `json:"videoCodec"`
	AudioCodec      string   `json:"audioCodec"`
	VideoTrackID    string   `json:"videoTrackId"`
	AudioTrackID    string   `json:"audioTrackId"`
	SegmentCount    int      `json:"segmentCount"`
	SegmentFiles    []string `json:"segmentFiles,omitempty"`
	LastSegmentPath string   `json:"lastSegmentPath"`
	HLSEnabled      bool     `json:"hlsEnabled"`
	HLSPlaylistPath string   `json:"hlsPlaylistPath"`
	HLSDir          string   `json:"hlsDir"`
	HLSSegmentCount int      `json:"hlsSegmentCount"`
	VideoPackets    uint64   `json:"videoPackets"`
	AudioPackets    uint64   `json:"audioPackets"`
	VideoBytes      uint64   `json:"videoBytes"`
	AudioBytes      uint64   `json:"audioBytes"`
	VideoRTPSpanSec float64  `json:"videoRtpSpanSec"`
	AudioRTPSpanSec float64  `json:"audioRtpSpanSec"`
	ArchiveEffectiveFPS float64 `json:"archiveEffectiveFps"`
	LowFrameRate        bool    `json:"lowFrameRate"`
	LastPacketAt    string   `json:"lastPacketAt"`
	LastError       string   `json:"lastError"`
	StopReason      string   `json:"stopReason"`
	GstPID          int      `json:"gstPid"`
}

type trackBinding struct {
	kind        string
	mimeType    string
	trackID     string
	payloadType uint8
	clockRate   uint32
	channels    uint16
	width       uint32
	height      uint32
	track       *webrtc.TrackRemote
	pliWriter   lksdk.PLIWriter
}

type pipelineConfig struct {
	outputPattern   string
	segmentSeconds  int
	statusPath      string
	connectTimeout  time.Duration
	trackWait       time.Duration
	scanInterval    time.Duration
	videoPort       int
	audioPort       int
	enableHLS       bool
	hlsDir          string
	hlsSegmentSeconds int
	hlsPlaylistLength int
	hlsMaxFiles       int
	hlsVideoPort    int
	hlsAudioPort    int
	archiveWidth    int
	archiveHeight   int
	archiveBitrateKbps int
	hlsWidth        int
	hlsHeight       int
	hlsBitrateKbps  int
}

type recorder struct {
	cfg      pipelineConfig
	statusMu sync.Mutex
	status   appStatus

	room *lksdk.Room

	video *trackBinding
	audio *trackBinding

	archiveVideo *archiveVideoSegmenter

	firstTrackCh chan struct{}
	firstOnce    sync.Once

	gstCmd    *exec.Cmd
	hlsCmd    *exec.Cmd

	stopOnce   sync.Once
	stopReason string
	stopErr    error
	stopCh     chan struct{}
}

type packetArchiveWriter interface {
	WriteRTP(packet *rtp.Packet) error
	Close() error
}

type sequentialIVFWriter struct {
	path               string
	file               *os.File
	codecMime          string
	width              uint16
	height             uint16
	startedAt          time.Time
	builder            *samplebuilder.SampleBuilder
	seenKeyFrame       bool
	frameCount         uint32
	recentFrameTimes   []time.Time
}

func newSequentialIVFWriter(path string, codecMime string, width uint16, height uint16, clockRate uint32, onPacketDropped func()) (*sequentialIVFWriter, error) {
	file, err := os.Create(path)
	if err != nil {
		return nil, err
	}
	var depacketizer rtp.Depacketizer
	var fourcc string
	switch strings.ToLower(codecMime) {
	case "video/vp8":
		depacketizer = &codecs.VP8Packet{}
		fourcc = "VP80"
	case "video/vp9":
		depacketizer = &codecs.VP9Packet{}
		fourcc = "VP90"
	default:
		_ = file.Close()
		return nil, fmt.Errorf("unsupported ivf codec: %s", codecMime)
	}
	w := &sequentialIVFWriter{
		path:      path,
		file:      file,
		codecMime: strings.ToLower(codecMime),
		width:     width,
		height:    height,
		startedAt: time.Now(),
	}
	w.builder = samplebuilder.New(
		archiveSampleMaxLate,
		depacketizer,
		clockRate,
		samplebuilder.WithPacketDroppedHandler(func() {
			w.seenKeyFrame = false
			if onPacketDropped != nil {
				onPacketDropped()
			}
		}),
	)
	if err := w.writeHeader(fourcc, 30, 1, 0); err != nil {
		_ = file.Close()
		return nil, err
	}
	return w, nil
}

func (w *sequentialIVFWriter) writeHeader(fourcc string, denominator uint32, numerator uint32, frameCount uint32) error {
	header := make([]byte, 32)
	copy(header[0:], "DKIF")
	binary.LittleEndian.PutUint16(header[4:], 0)
	binary.LittleEndian.PutUint16(header[6:], 32)
	copy(header[8:], fourcc)
	binary.LittleEndian.PutUint16(header[12:], w.width)
	binary.LittleEndian.PutUint16(header[14:], w.height)
	binary.LittleEndian.PutUint32(header[16:], denominator)
	binary.LittleEndian.PutUint32(header[20:], numerator)
	binary.LittleEndian.PutUint32(header[24:], frameCount)
	binary.LittleEndian.PutUint32(header[28:], 0)
	if _, err := w.file.Seek(0, io.SeekStart); err != nil {
		return err
	}
	_, err := w.file.Write(header)
	return err
}

func (w *sequentialIVFWriter) writeFrame(frame []byte) error {
	if len(frame) == 0 {
		return nil
	}
	frameHeader := make([]byte, 12)
	binary.LittleEndian.PutUint32(frameHeader[0:], uint32(len(frame)))
	binary.LittleEndian.PutUint64(frameHeader[4:], uint64(w.frameCount))
	if _, err := w.file.Seek(0, io.SeekEnd); err != nil {
		return err
	}
	if _, err := w.file.Write(frameHeader); err != nil {
		return err
	}
	if _, err := w.file.Write(frame); err != nil {
		return err
	}
	w.frameCount++
	now := time.Now()
	w.recentFrameTimes = append(w.recentFrameTimes, now)
	cutoff := now.Add(-effectiveFPSWindow)
	trimIndex := 0
	for trimIndex < len(w.recentFrameTimes) && w.recentFrameTimes[trimIndex].Before(cutoff) {
		trimIndex++
	}
	if trimIndex > 0 {
		w.recentFrameTimes = append([]time.Time(nil), w.recentFrameTimes[trimIndex:]...)
	}
	return nil
}

func (w *sequentialIVFWriter) isKeyframeSample(frame []byte) bool {
	if len(frame) == 0 {
		return false
	}
	switch w.codecMime {
	case "video/vp8":
		return frame[0]&0x01 == 0
	case "video/vp9":
		return true
	default:
		return false
	}
}

func (w *sequentialIVFWriter) WriteRTP(packet *rtp.Packet) error {
	if packet == nil || w.builder == nil {
		return nil
	}
	w.builder.Push(packet)
	for {
		sample, _ := w.builder.PopWithTimestamp()
		if sample == nil {
			break
		}
		if !w.seenKeyFrame {
			if !w.isKeyframeSample(sample.Data) {
				continue
			}
			w.seenKeyFrame = true
		}
		if err := w.writeFrame(sample.Data); err != nil {
			return err
		}
	}
	return nil
}

func (w *sequentialIVFWriter) Close() error {
	if w.file == nil {
		return nil
	}
	if w.builder != nil {
		for {
			sample, _ := w.builder.ForcePopWithTimestamp()
			if sample == nil {
				break
			}
			if !w.seenKeyFrame {
				if !w.isKeyframeSample(sample.Data) {
					continue
				}
				w.seenKeyFrame = true
			}
			if err := w.writeFrame(sample.Data); err != nil {
				_ = w.file.Close()
				w.file = nil
				return err
			}
		}
	}
	err := w.patchHeader()
	closeErr := w.file.Close()
	w.file = nil
	if err != nil {
		return err
	}
	return closeErr
}

func (w *sequentialIVFWriter) patchHeader() error {
	if w.file == nil || w.frameCount == 0 {
		return nil
	}
	denominator := uint32(30)
	numerator := uint32(1)
	if w.frameCount > 1 {
		seconds := time.Since(w.startedAt).Seconds()
		if seconds > 0 {
			fps := float64(w.frameCount-1) / seconds
			if fps >= 1 && fps <= 120 {
				denominator = uint32(maxInt(1, int(fps+0.5)))
			}
		}
	}
	fourcc := "VP80"
	if w.codecMime == "video/vp9" {
		fourcc = "VP90"
	}
	if err := w.writeHeader(fourcc, denominator, numerator, w.frameCount); err != nil {
		return err
	}
	_, err := w.file.Seek(0, io.SeekEnd)
	return err
}

func (w *sequentialIVFWriter) CurrentFPS() float64 {
	if w == nil || len(w.recentFrameTimes) < 2 {
		return 0
	}
	first := w.recentFrameTimes[0]
	last := w.recentFrameTimes[len(w.recentFrameTimes)-1]
	seconds := last.Sub(first).Seconds()
	if seconds <= 0 {
		return 0
	}
	return float64(len(w.recentFrameTimes)-1) / seconds
}

type h264PacketWriter struct {
	writer *h264writer.H264Writer
}

func (w *h264PacketWriter) WriteRTP(packet *rtp.Packet) error {
	if packet == nil {
		return nil
	}
	return w.writer.WriteRTP(packet)
}

func (w *h264PacketWriter) Close() error {
	if w.writer == nil {
		return nil
	}
	return w.writer.Close()
}

type archiveVideoSegmenter struct {
	mu             sync.Mutex
	outputPattern  string
	segmentSeconds int
	mimeType       string
	clockRate      uint32
	width          int
	height         int
	requestKeyFrame func()
	packetWriter   packetArchiveWriter
	builder        *samplebuilder.SampleBuilder
	segmentIndex   int
	segmentStarted time.Time
	rotatePending  bool
}

func (s *archiveVideoSegmenter) currentEffectiveFPS() float64 {
	s.mu.Lock()
	defer s.mu.Unlock()
	writer, ok := s.packetWriter.(*sequentialIVFWriter)
	if !ok {
		return 0
	}
	return writer.CurrentFPS()
}

func main() {
	var cfg pipelineConfig
	var serverHost string
	var token string

	flag.StringVar(&serverHost, "server-host", "", "LiveKit server host")
	flag.StringVar(&token, "token", "", "subscriber token")
	flag.StringVar(&cfg.outputPattern, "output-pattern", "", "segment output pattern, e.g. /tmp/out__seg_%05d.mkv")
	flag.StringVar(&cfg.statusPath, "status-path", "", "status json path")
	flag.IntVar(&cfg.segmentSeconds, "segment-seconds", 300, "segment duration in seconds")
	flag.DurationVar(&cfg.connectTimeout, "connect-timeout", 45*time.Second, "room connect timeout")
	flag.DurationVar(&cfg.trackWait, "track-wait", 12*time.Second, "extra wait after first track to collect audio/video pair")
	flag.DurationVar(&cfg.scanInterval, "scan-interval", 2*time.Second, "segment scan interval")
	flag.BoolVar(&cfg.enableHLS, "enable-hls", false, "enable HLS preview sidecar output")
	flag.StringVar(&cfg.hlsDir, "hls-dir", "", "HLS output directory")
	flag.IntVar(&cfg.hlsSegmentSeconds, "hls-segment-seconds", 6, "HLS target segment duration in seconds")
	flag.IntVar(&cfg.hlsPlaylistLength, "hls-playlist-length", 6, "HLS playlist length")
	flag.IntVar(&cfg.hlsMaxFiles, "hls-max-files", 24, "max HLS files to keep on disk")
	flag.IntVar(&cfg.archiveWidth, "archive-width", defaultArchiveOutputWidth, "archive output width")
	flag.IntVar(&cfg.archiveHeight, "archive-height", defaultArchiveOutputHeight, "archive output height")
	flag.IntVar(&cfg.archiveBitrateKbps, "archive-bitrate-kbps", defaultArchiveVideoBitrateKbps, "archive video bitrate kbps")
	flag.IntVar(&cfg.hlsWidth, "hls-width", defaultHLSOutputWidth, "HLS output width")
	flag.IntVar(&cfg.hlsHeight, "hls-height", defaultHLSOutputHeight, "HLS output height")
	flag.IntVar(&cfg.hlsBitrateKbps, "hls-bitrate-kbps", defaultHLSVideoBitrateKbps, "HLS video bitrate kbps")
	flag.Parse()

	if serverHost == "" || token == "" || cfg.outputPattern == "" || cfg.statusPath == "" {
		fmt.Fprintln(os.Stderr, "missing required flags: --server-host --token --output-pattern --status-path")
		os.Exit(2)
	}

	videoPort, err := reserveUDPPort()
	if err != nil {
		fmt.Fprintln(os.Stderr, "reserve video port failed:", err)
		os.Exit(1)
	}
	audioPort, err := reserveUDPPort()
	if err != nil {
		fmt.Fprintln(os.Stderr, "reserve audio port failed:", err)
		os.Exit(1)
	}
	cfg.videoPort = videoPort
	cfg.audioPort = audioPort
	if cfg.enableHLS {
		hlsVideoPort, err := reserveUDPPort()
		if err != nil {
			fmt.Fprintln(os.Stderr, "reserve HLS video port failed:", err)
			os.Exit(1)
		}
		cfg.hlsVideoPort = hlsVideoPort
		hlsAudioPort, err := reserveUDPPort()
		if err != nil {
			fmt.Fprintln(os.Stderr, "reserve HLS audio port failed:", err)
			os.Exit(1)
		}
		cfg.hlsAudioPort = hlsAudioPort
	}

	rec := &recorder{
		cfg:         cfg,
		firstTrackCh: make(chan struct{}),
		stopCh:      make(chan struct{}),
		status: appStatus{
			StartedAt:  nowISO(),
			UpdatedAt:  nowISO(),
			State:      "initializing",
			ServerHost: serverHost,
			HLSEnabled: cfg.enableHLS,
			HLSDir:     cfg.hlsDir,
		},
	}
	rec.writeStatus()

	ctx, cancel := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer cancel()

	go rec.statusLoop(ctx)

	if err := rec.connectRoom(serverHost, token); err != nil {
		rec.fail("signal_connect_failed", err)
		os.Exit(1)
	}
	defer rec.disconnectRoom()

	if err := rec.waitForTracks(ctx); err != nil {
		reason := "track_wait_failed"
		if strings.Contains(strings.ToLower(err.Error()), "no video track") {
			reason = "no_video_track"
		}
		rec.fail(reason, err)
		os.Exit(1)
	}

	if err := rec.startPipeline(); err != nil {
		rec.fail("gst_start_failed", err)
		os.Exit(1)
	}

	if err := rec.startForwarders(); err != nil {
		rec.fail("forwarder_start_failed", err)
		os.Exit(1)
	}

	rec.setState("recording")

	select {
	case <-ctx.Done():
		rec.stop("signal_stop", nil)
	case <-rec.stopCh:
	}
	if rec.gstCmd != nil && rec.gstCmd.ProcessState == nil {
		_ = rec.gstCmd.Wait()
	}
	if rec.hlsCmd != nil && rec.hlsCmd.ProcessState == nil {
		_ = rec.hlsCmd.Wait()
	}
	rec.setFinalState()
	if rec.stopErr != nil {
		os.Exit(1)
	}
}

func (r *recorder) connectRoom(serverHost, token string) error {
	cb := &lksdk.RoomCallback{
		OnDisconnectedWithReason: func(reason lksdk.DisconnectionReason) {
			r.updateStatus(func(s *appStatus) {
				s.LastError = string(reason)
			})
			r.stop("room_disconnected", nil)
		},
		OnReconnecting: func() {
			r.updateStatus(func(s *appStatus) {
				s.State = "reconnecting"
				s.Connected = false
			})
		},
		OnReconnected: func() {
			r.updateStatus(func(s *appStatus) {
				if s.State != "recording" {
					s.State = "connected"
				}
				s.Connected = true
			})
		},
		OnRoomMetadataChanged: func(metadata string) {},
		ParticipantCallback: lksdk.ParticipantCallback{
			OnTrackPublished: func(publication *lksdk.RemoteTrackPublication, rp *lksdk.RemoteParticipant) {
				if publication == nil {
					return
				}
				if publication.Kind() == lksdk.TrackKindVideo {
					_ = publication.SetVideoQuality(livekit.VideoQuality_HIGH)
					publication.SetVideoDimensions(1920, 1080)
				}
				_ = publication.SetSubscribed(true)
			},
			OnTrackSubscribed: func(track *webrtc.TrackRemote, publication *lksdk.RemoteTrackPublication, rp *lksdk.RemoteParticipant) {
				r.onTrackSubscribed(track, publication, rp)
			},
			OnTrackSubscriptionFailed: func(sid string, rp *lksdk.RemoteParticipant) {
				r.updateStatus(func(s *appStatus) {
					s.LastError = fmt.Sprintf("track subscription failed: sid=%s participant=%s", sid, rp.Identity())
				})
			},
		},
	}

	room, err := lksdk.ConnectToRoomWithToken(
		serverHost,
		token,
		cb,
		lksdk.WithAutoSubscribe(false),
		lksdk.WithConnectTimeout(r.cfg.connectTimeout),
	)
	if err != nil {
		return err
	}
	r.room = room
	r.updateStatus(func(s *appStatus) {
		s.State = "connected"
		s.Connected = true
		s.RoomName = room.Name()
	})
	return nil
}

func (r *recorder) disconnectRoom() {
	if r.room != nil {
		r.room.Disconnect()
	}
}

func (r *recorder) onTrackSubscribed(track *webrtc.TrackRemote, publication *lksdk.RemoteTrackPublication, rp *lksdk.RemoteParticipant) {
	if publication != nil && kindString(track.Kind()) == "video" {
		_ = publication.SetVideoQuality(livekit.VideoQuality_HIGH)
		preferredWidth := uint32(maxInt(r.cfg.archiveWidth, r.cfg.hlsWidth))
		preferredHeight := uint32(maxInt(r.cfg.archiveHeight, r.cfg.hlsHeight))
		if preferredWidth > 0 && preferredHeight > 0 {
			publication.SetVideoDimensions(preferredWidth, preferredHeight)
		}
	}
	mime := strings.ToLower(publication.MimeType())
	if mime == "" {
		mime = strings.ToLower(track.Codec().MimeType)
	}
	if mime == "" && kindString(track.Kind()) == "audio" {
		mime = "audio/opus"
	}
	if !isSupportedMime(mime) {
		return
	}
	binding := &trackBinding{
		kind:        kindString(track.Kind()),
		mimeType:    mime,
		trackID:     track.ID(),
		payloadType: uint8(track.PayloadType()),
		clockRate:   track.Codec().ClockRate,
		channels:    track.Codec().Channels,
		track:       track,
		pliWriter:   rp.WritePLI,
	}
	if publication != nil {
		if info := publication.TrackInfo(); info != nil {
			binding.width = info.GetWidth()
			binding.height = info.GetHeight()
		}
	}

	accepted := false
	r.statusMu.Lock()
	switch binding.kind {
	case "video":
		if r.video == nil {
			r.video = binding
			accepted = true
			r.status.VideoCodec = binding.mimeType
			r.status.VideoTrackID = binding.trackID
		}
	case "audio":
		if r.audio == nil && strings.Contains(binding.mimeType, "opus") {
			r.audio = binding
			accepted = true
			r.status.AudioCodec = binding.mimeType
			r.status.AudioTrackID = binding.trackID
		}
	}
	r.statusMu.Unlock()

	if accepted {
		r.firstOnce.Do(func() { close(r.firstTrackCh) })
		r.writeStatus()
	}
}

func (r *recorder) waitForTracks(ctx context.Context) error {
	select {
	case <-r.firstTrackCh:
	case <-time.After(r.cfg.connectTimeout):
		return errors.New("did not receive any supported media track before timeout")
	case <-ctx.Done():
		return ctx.Err()
	}

	select {
	case <-ctx.Done():
		return ctx.Err()
	default:
	}

	deadline := time.Now().Add(r.cfg.trackWait)
	for {
		r.statusMu.Lock()
		videoReady := r.video != nil
		audioReady := r.audio != nil
		r.statusMu.Unlock()

		if videoReady && audioReady {
			return nil
		}
		if time.Now().After(deadline) {
			r.statusMu.Lock()
			defer r.statusMu.Unlock()
			if r.video == nil && r.audio == nil {
				return errors.New("no supported tracks after first-track wait window")
			}
			if r.video == nil {
				return errors.New("no video track after first-track wait window")
			}
			return nil
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		case <-time.After(250 * time.Millisecond):
		}
	}
}

func (r *recorder) startPipeline() error {
	if r.video == nil {
		return errors.New("no supported video track for archive pipeline")
	}
	archivePattern, err := archivePatternForMime(r.cfg.outputPattern, r.video.mimeType)
	if err != nil {
		return err
	}
	r.cfg.outputPattern = archivePattern
	segmenter, err := newArchiveVideoSegmenter(r.video, archivePattern, r.cfg.segmentSeconds, r.cfg.archiveWidth, r.cfg.archiveHeight)
	if err != nil {
		return err
	}
	r.archiveVideo = segmenter
	r.updateStatus(func(s *appStatus) {
		s.GstPID = 0
	})

	if r.cfg.enableHLS && r.cfg.hlsDir != "" {
		if err := os.MkdirAll(r.cfg.hlsDir, 0o755); err != nil {
			return err
		}
		hlsArgs, err := r.buildHLSArgs()
		if err != nil {
			return err
		}
		hlsCmd := exec.Command("/opt/homebrew/bin/gst-launch-1.0", hlsArgs...)
		hlsCmd.Stdout = os.Stdout
		hlsCmd.Stderr = os.Stderr
		if err := hlsCmd.Start(); err != nil {
			return err
		}
		r.hlsCmd = hlsCmd
		r.updateStatus(func(s *appStatus) {
			s.GstPID = hlsCmd.Process.Pid
		})
		go func() {
			err := hlsCmd.Wait()
			if r.stopReason != "" {
				return
			}
			if err != nil {
				r.updateStatus(func(s *appStatus) {
					s.LastError = fmt.Sprintf("hls_exit: %v", err)
				})
			}
		}()
	}
	return nil
}

func (r *recorder) buildGStreamerArgs() ([]string, error) {
	args := []string{
		"-e",
		"splitmuxsink",
		"name=sink",
		fmt.Sprintf("location=%s", r.cfg.outputPattern),
		"async-finalize=true",
		"muxer-factory=matroskamux",
		fmt.Sprintf("max-size-time=%d", uint64(r.cfg.segmentSeconds)*1_000_000_000),
		"send-keyframe-requests=true",
		"reset-muxer=true",
	}

	if r.video != nil {
		videoCaps, depayElems, err := gstreamerRTPPath(r.video)
		if err != nil {
			return nil, err
		}
		args = append(args,
			"udpsrc",
			fmt.Sprintf("port=%d", r.cfg.videoPort),
			fmt.Sprintf("buffer-size=%d", localUDPBufferBytes),
			fmt.Sprintf("caps=%s", videoCaps),
			"!",
			"rtpjitterbuffer",
			"latency=500",
			"!",
		)
		args = append(args, depayElems...)
		args = append(args, "!")
		args = append(args, videoDecodeChain(r.video.mimeType)...)
		args = append(args,
			"!",
			"videoscale",
			"!",
			fmt.Sprintf("video/x-raw,width=%d,height=%d", r.cfg.archiveWidth, r.cfg.archiveHeight),
			"!",
			"videoconvert",
		)
		args = append(args, videoEncodeChain(r.cfg.archiveBitrateKbps, maxInt(60, r.cfg.segmentSeconds*30))...)
		args = append(args, "!", "queue", "!", "sink.video")
	}

	if r.audio != nil {
		audioCaps, depayElems, err := gstreamerRTPPath(r.audio)
		if err != nil {
			return nil, err
		}
		args = append(args,
			"udpsrc",
			fmt.Sprintf("port=%d", r.cfg.audioPort),
			fmt.Sprintf("buffer-size=%d", localUDPBufferBytes),
			fmt.Sprintf("caps=%s", audioCaps),
			"!",
			"rtpjitterbuffer",
			"latency=500",
			"!",
		)
		args = append(args, depayElems...)
		args = append(args, "!", "queue", "!", "sink.audio_0")
	}

	if r.video == nil && r.audio == nil {
		return nil, errors.New("no supported audio/video track for gst pipeline")
	}
	return args, nil
}

func (r *recorder) buildHLSArgs() ([]string, error) {
	playlistPath := filepath.Join(r.cfg.hlsDir, "playlist.m3u8")
	locationPattern := filepath.Join(r.cfg.hlsDir, "segment_%05d.ts")
	args := []string{
		"-e",
		"hlssink2",
		"name=hls",
		fmt.Sprintf("playlist-location=%s", playlistPath),
		fmt.Sprintf("location=%s", locationPattern),
		fmt.Sprintf("target-duration=%d", maxInt(2, r.cfg.hlsSegmentSeconds)),
		fmt.Sprintf("playlist-length=%d", maxInt(3, r.cfg.hlsPlaylistLength)),
		fmt.Sprintf("max-files=%d", maxInt(6, r.cfg.hlsMaxFiles)),
		"send-keyframe-requests=true",
	}
	if r.video != nil {
		videoCaps, depayElems, err := gstreamerRTPPath(r.video)
		if err != nil {
			return nil, err
		}
		args = append(args,
			"udpsrc",
			fmt.Sprintf("port=%d", r.cfg.hlsVideoPort),
			fmt.Sprintf("buffer-size=%d", localUDPBufferBytes),
			fmt.Sprintf("caps=%s", videoCaps),
			"!",
			"rtpjitterbuffer",
			"latency=500",
			"!",
		)
		args = append(args, depayElems...)
		args = append(args, "!")
		args = append(args, videoDecodeChain(r.video.mimeType)...)
		args = append(args,
			"!",
			"videoscale",
			"!",
			fmt.Sprintf("video/x-raw,width=%d,height=%d", r.cfg.hlsWidth, r.cfg.hlsHeight),
			"!",
			"videoconvert",
		)
		args = append(args, videoEncodeChain(r.cfg.hlsBitrateKbps, maxInt(30, r.cfg.hlsSegmentSeconds*30))...)
		args = append(args, "!", "queue", "!", "hls.video")
	}
	if r.audio != nil {
		audioCaps, depayElems, err := gstreamerRTPPath(r.audio)
		if err != nil {
			return nil, err
		}
		args = append(args,
			"udpsrc",
			fmt.Sprintf("port=%d", r.cfg.hlsAudioPort),
			fmt.Sprintf("buffer-size=%d", localUDPBufferBytes),
			fmt.Sprintf("caps=%s", audioCaps),
			"!",
			"rtpjitterbuffer",
			"latency=500",
			"!",
		)
		args = append(args, depayElems...)
		args = append(args,
			"!",
			"opusdec",
			"!",
			"audioconvert",
			"!",
			"audioresample",
			"!",
			"avenc_aac",
			"bitrate=128000",
			"!",
			"aacparse",
			"!",
			"queue",
			"!",
			"hls.audio",
		)
	}
	if r.video == nil && r.audio == nil {
		return nil, errors.New("no supported audio/video track for HLS pipeline")
	}
	return args, nil
}

func (r *recorder) startForwarders() error {
	if r.video != nil {
		videoPorts := make([]udpTarget, 0, 1)
		if r.cfg.enableHLS && r.cfg.hlsVideoPort > 0 {
			videoPorts = append(videoPorts, udpTarget{port: r.cfg.hlsVideoPort, required: false, label: "hls_video"})
		}
		go r.forwardTrack(r.video, videoPorts)
		go r.videoPLIKeepalive(r.video)
	}
	if r.audio != nil {
		audioPorts := make([]udpTarget, 0, 1)
		if r.cfg.enableHLS && r.cfg.hlsAudioPort > 0 {
			audioPorts = append(audioPorts, udpTarget{port: r.cfg.hlsAudioPort, required: false, label: "hls_audio"})
		}
		if len(audioPorts) > 0 {
			go r.forwardTrack(r.audio, audioPorts)
		}
	}
	return nil
}

func (r *recorder) videoPLIKeepalive(track *trackBinding) {
	if track == nil || track.pliWriter == nil || track.kind != "video" {
		return
	}
	ticker := time.NewTicker(videoPLIBurstInterval)
	defer ticker.Stop()
	startedAt := time.Now()
	track.pliWriter(track.track.SSRC())
	for {
		select {
		case <-ticker.C:
			track.pliWriter(track.track.SSRC())
			if time.Since(startedAt) >= videoPLIBurstDuration {
				ticker.Reset(videoPLISteadyInterval)
			}
		case <-r.stopCh:
			return
		}
	}
}

type udpTarget struct {
	port     int
	required bool
	label    string
}

type udpWriter struct {
	target udpTarget
	addr   *net.UDPAddr
	conn   *net.UDPConn
}

func (r *recorder) forwardTrack(track *trackBinding, targets []udpTarget) {
	writers := make([]*udpWriter, 0, len(targets))
	for _, target := range targets {
		addr := &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: target.port}
		conn, err := net.DialUDP("udp4", nil, addr)
		if err != nil {
			if target.required {
				r.stop("udp_connect_failed", err)
				return
			}
			continue
		}
		_ = conn.SetWriteBuffer(localUDPBufferBytes)
		writers = append(writers, &udpWriter{target: target, addr: addr, conn: conn})
	}
	hasArchiveSink := track.kind == "video" && r.archiveVideo != nil
	if len(writers) == 0 && !hasArchiveSink {
		r.stop("udp_connect_failed", errors.New("no udp writer available"))
		return
	}
	defer func() {
		for _, writer := range writers {
			if writer.conn != nil {
				_ = writer.conn.Close()
			}
		}
	}()
	startedAt := time.Now()
	var packetCount uint64
	var byteCount uint64
	var firstTimestamp uint32
	var lastTimestamp uint32
	lastFlush := time.Now()

	for {
		pkt, _, err := track.track.ReadRTP()
		if err != nil {
			if r.stopReason != "" {
				return
			}
			if errors.Is(err, io.EOF) || strings.Contains(strings.ToLower(err.Error()), "eof") {
				r.stop("track_eof", nil)
				return
			}
			r.stop("track_read_failed", err)
			return
		}
		if hasArchiveSink {
			if err := r.archiveVideo.Push(pkt); err != nil {
				r.stop("archive_write_failed", err)
				return
			}
		}
		raw, err := pkt.Marshal()
		if err != nil {
			continue
		}
		activeWriters := writers[:0]
		for _, writer := range writers {
			if writer.conn == nil {
				continue
			}
			if _, err := writer.conn.Write(raw); err != nil {
				if time.Since(startedAt) <= udpStartupGracePeriod && isRetryableLocalUDPError(err) {
					_ = writer.conn.Close()
					time.Sleep(300 * time.Millisecond)
					conn, dialErr := net.DialUDP("udp4", nil, writer.addr)
					if dialErr == nil {
						_ = conn.SetWriteBuffer(localUDPBufferBytes)
						writer.conn = conn
						activeWriters = append(activeWriters, writer)
						continue
					}
				}
				if writer.target.required {
					r.stop("udp_write_failed", err)
					return
				}
				continue
			}
			activeWriters = append(activeWriters, writer)
		}
		writers = activeWriters
		if len(writers) == 0 && !hasArchiveSink {
			r.stop("udp_write_failed", errors.New("all udp outputs unavailable"))
			return
		}
		if packetCount == 0 {
			firstTimestamp = pkt.Timestamp
		}
		packetCount++
		byteCount += uint64(len(raw))
		lastTimestamp = pkt.Timestamp
		if time.Since(lastFlush) >= time.Second {
			r.recordPacketStats(track, packetCount, byteCount, firstTimestamp, lastTimestamp)
			lastFlush = time.Now()
		}
	}
}

func (r *recorder) recordPacketStats(track *trackBinding, packets uint64, bytes uint64, firstTimestamp uint32, lastTimestamp uint32) {
	span := 0.0
	if packets > 1 && track.clockRate > 0 {
		if lastTimestamp >= firstTimestamp {
			span = float64(lastTimestamp-firstTimestamp) / float64(track.clockRate)
		}
	}
	r.updateStatus(func(s *appStatus) {
		if track.kind == "video" {
			s.VideoPackets = packets
			s.VideoBytes = bytes
			s.VideoRTPSpanSec = span
		} else if track.kind == "audio" {
			s.AudioPackets = packets
			s.AudioBytes = bytes
			s.AudioRTPSpanSec = span
		}
		s.LastPacketAt = nowISO()
	})
}

func (r *recorder) statusLoop(ctx context.Context) {
	ticker := time.NewTicker(r.cfg.scanInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ticker.C:
			r.scanSegments()
			r.writeStatus()
		case <-ctx.Done():
			r.scanSegments()
			r.writeStatus()
			return
		}
	}
}

func (r *recorder) scanSegments() {
	dir := filepath.Dir(r.cfg.outputPattern)
	base := filepath.Base(r.cfg.outputPattern)
	pattern := strings.Replace(base, "%05d", "*", 1)
	if pattern == base {
		pattern = strings.Replace(base, "%d", "*", 1)
	}
	matches, _ := filepath.Glob(filepath.Join(dir, pattern))
	sort.Strings(matches)
	segmentFiles := make([]string, 0, len(matches))
	for _, item := range matches {
		segmentFiles = append(segmentFiles, filepath.Base(item))
	}
	lastSegment := ""
	if len(segmentFiles) > 0 {
		lastSegment = filepath.Join(dir, segmentFiles[len(segmentFiles)-1])
	}
	effectiveFPS := 0.0
	lowFrameRate := false
	if r.archiveVideo != nil {
		effectiveFPS = r.archiveVideo.currentEffectiveFPS()
		if effectiveFPS > 0 && effectiveFPS < lowFrameRateThreshold {
			lowFrameRate = true
		}
	}
	r.updateStatus(func(s *appStatus) {
		s.SegmentCount = len(segmentFiles)
		s.SegmentFiles = segmentFiles
		s.LastSegmentPath = lastSegment
		s.ArchiveEffectiveFPS = effectiveFPS
		s.LowFrameRate = lowFrameRate
		if r.cfg.enableHLS && r.cfg.hlsDir != "" {
			hlsSegments, _ := filepath.Glob(filepath.Join(r.cfg.hlsDir, "*.ts"))
			sort.Strings(hlsSegments)
			s.HLSDir = r.cfg.hlsDir
			s.HLSPlaylistPath = filepath.Join(r.cfg.hlsDir, "playlist.m3u8")
			s.HLSSegmentCount = len(hlsSegments)
		}
	})
}

func (r *recorder) setState(state string) {
	r.updateStatus(func(s *appStatus) {
		s.State = state
	})
}

func (r *recorder) updateStatus(fn func(*appStatus)) {
	r.statusMu.Lock()
	defer r.statusMu.Unlock()
	fn(&r.status)
	r.status.UpdatedAt = nowISO()
	r.writeStatusLocked()
}

func (r *recorder) writeStatus() {
	r.statusMu.Lock()
	defer r.statusMu.Unlock()
	r.writeStatusLocked()
}

func (r *recorder) writeStatusLocked() {
	tmp := r.cfg.statusPath + ".tmp"
	data, _ := json.MarshalIndent(r.status, "", "  ")
	_ = os.WriteFile(tmp, data, 0o644)
	_ = os.Rename(tmp, r.cfg.statusPath)
}

func (r *recorder) fail(reason string, err error) {
	r.stop(reason, err)
	r.setFinalState()
}

func (r *recorder) stop(reason string, err error) {
	r.stopOnce.Do(func() {
		r.stopReason = reason
		r.stopErr = err
		if r.archiveVideo != nil {
			if closeErr := r.archiveVideo.Close(); closeErr != nil && err == nil {
				r.stopErr = closeErr
				err = closeErr
			}
		}
		if r.gstCmd != nil && r.gstCmd.Process != nil {
			_ = r.gstCmd.Process.Signal(os.Interrupt)
		}
		if r.hlsCmd != nil && r.hlsCmd.Process != nil {
			_ = r.hlsCmd.Process.Signal(os.Interrupt)
		}
		r.updateStatus(func(s *appStatus) {
			s.StopReason = reason
			if err != nil {
				s.LastError = err.Error()
			}
		})
		close(r.stopCh)
	})
}

func (r *recorder) setFinalState() {
	finalState := "completed"
	if r.stopErr != nil {
		finalState = "failed"
	}
	r.updateStatus(func(s *appStatus) {
		s.State = finalState
		s.Connected = false
		if s.StopReason == "" {
			s.StopReason = r.stopReason
		}
		if r.stopErr != nil && s.LastError == "" {
			s.LastError = r.stopErr.Error()
		}
	})
}

func reserveUDPPort() (int, error) {
	l, err := net.ListenUDP("udp4", &net.UDPAddr{IP: net.ParseIP("127.0.0.1"), Port: 0})
	if err != nil {
		return 0, err
	}
	defer l.Close()
	return l.LocalAddr().(*net.UDPAddr).Port, nil
}

func kindString(kind webrtc.RTPCodecType) string {
	switch kind {
	case webrtc.RTPCodecTypeAudio:
		return "audio"
	case webrtc.RTPCodecTypeVideo:
		return "video"
	default:
		return "unknown"
	}
}

func isSupportedMime(mime string) bool {
	switch strings.ToLower(mime) {
	case "video/vp8", "video/vp9", "video/h264", "audio/opus":
		return true
	default:
		return false
	}
}

func gstreamerRTPPath(track *trackBinding) (string, []string, error) {
	payload := track.payloadType
	switch strings.ToLower(track.mimeType) {
	case "video/vp8":
		return fmt.Sprintf("application/x-rtp,media=video,encoding-name=VP8,payload=%d,clock-rate=%d", payload, track.clockRate),
			[]string{"rtpvp8depay", "request-keyframe=true", "wait-for-keyframe=true"},
			nil
	case "video/vp9":
		return fmt.Sprintf("application/x-rtp,media=video,encoding-name=VP9,payload=%d,clock-rate=%d", payload, track.clockRate),
			[]string{"rtpvp9depay", "request-keyframe=true", "wait-for-keyframe=true"},
			nil
	case "video/h264":
		return fmt.Sprintf("application/x-rtp,media=video,encoding-name=H264,payload=%d,clock-rate=%d", payload, track.clockRate),
			[]string{"rtph264depay", "request-keyframe=true", "wait-for-keyframe=true", "!", "h264parse", "config-interval=-1"},
			nil
	case "audio/opus":
		channels := track.channels
		if channels == 0 {
			channels = 2
		}
		return fmt.Sprintf("application/x-rtp,media=audio,encoding-name=OPUS,payload=%d,clock-rate=%d,encoding-params=%d", payload, track.clockRate, channels),
			[]string{"rtpopusdepay", "!", "opusparse"},
			nil
	default:
		return "", nil, fmt.Errorf("unsupported mime for gst pipeline: %s", track.mimeType)
	}
}

func videoDecodeChain(mime string) []string {
	switch strings.ToLower(mime) {
	case "video/vp8":
		return []string{"vp8dec"}
	case "video/vp9":
		return []string{"vp9dec"}
	case "video/h264":
		return []string{"avdec_h264"}
	default:
		return []string{"decodebin"}
	}
}

func archivePatternForMime(outputPattern string, mime string) (string, error) {
	ext := ".mkv"
	switch strings.ToLower(mime) {
	case "video/vp8", "video/vp9":
		ext = ".ivf"
	case "video/h264":
		ext = ".h264"
	default:
		return "", fmt.Errorf("unsupported archive mime: %s", mime)
	}
	currentExt := filepath.Ext(outputPattern)
	if currentExt == "" {
		return outputPattern + ext, nil
	}
	return strings.TrimSuffix(outputPattern, currentExt) + ext, nil
}

func newArchiveVideoSegmenter(track *trackBinding, outputPattern string, segmentSeconds int, fallbackWidth int, fallbackHeight int) (*archiveVideoSegmenter, error) {
	var depacketizer rtp.Depacketizer
	switch strings.ToLower(track.mimeType) {
	case "video/vp8":
		depacketizer = &codecs.VP8Packet{}
	case "video/vp9":
		depacketizer = &codecs.VP9Packet{}
	case "video/h264":
		depacketizer = &codecs.H264Packet{}
	default:
		return nil, fmt.Errorf("unsupported archive mime: %s", track.mimeType)
	}
	width := int(track.width)
	height := int(track.height)
	if width <= 0 {
		width = fallbackWidth
	}
	if height <= 0 {
		height = fallbackHeight
	}
	s := &archiveVideoSegmenter{
		outputPattern:  outputPattern,
		segmentSeconds: segmentSeconds,
		mimeType:       strings.ToLower(track.mimeType),
		clockRate:      track.clockRate,
		width:          width,
		height:         height,
	}
	if track.pliWriter != nil && track.track != nil {
		ssrc := track.track.SSRC()
		s.requestKeyFrame = func() {
			track.pliWriter(ssrc)
		}
	}
	s.builder = samplebuilder.New(
		archiveSampleMaxLate,
		depacketizer,
		track.clockRate,
		samplebuilder.WithPacketDroppedHandler(func() {
			if track.pliWriter != nil {
				track.pliWriter(track.track.SSRC())
			}
		}),
	)
	return s, s.rotateLocked()
}

func (s *archiveVideoSegmenter) Push(packet *rtp.Packet) error {
	if packet == nil {
		return nil
	}
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.mimeType == "video/vp8" || s.mimeType == "video/vp9" {
		if s.packetWriter == nil {
			if err := s.rotateLocked(); err != nil {
				return err
			}
		}
		if s.rotatePending && s.isKeyframePacket(packet) {
			if err := s.rotateLocked(); err != nil {
				return err
			}
		}
		if err := s.packetWriter.WriteRTP(packet); err != nil {
			return err
		}
		if packet.Marker && s.segmentSeconds > 0 && time.Since(s.segmentStarted) >= time.Duration(s.segmentSeconds)*time.Second {
			s.rotatePending = true
		}
		return nil
	}
	s.builder.Push(packet)
	for _, pkt := range s.builder.PopPackets() {
		if s.packetWriter == nil {
			if err := s.rotateLocked(); err != nil {
				return err
			}
		}
		if s.rotatePending && s.isKeyframePacket(pkt) {
			if err := s.rotateLocked(); err != nil {
				return err
			}
		}
		if err := s.packetWriter.WriteRTP(pkt); err != nil {
			return err
		}
		if pkt.Marker && s.segmentSeconds > 0 && time.Since(s.segmentStarted) >= time.Duration(s.segmentSeconds)*time.Second {
			s.rotatePending = true
		}
	}
	return nil
}

func (s *archiveVideoSegmenter) Close() error {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.mimeType == "video/vp8" || s.mimeType == "video/vp9" {
		if s.packetWriter != nil {
			err := s.packetWriter.Close()
			s.packetWriter = nil
			return err
		}
		return nil
	}
	for _, pkt := range s.builder.ForcePopPackets() {
		if s.packetWriter == nil {
			if err := s.rotateLocked(); err != nil {
				return err
			}
		}
		if s.rotatePending && s.isKeyframePacket(pkt) {
			if err := s.rotateLocked(); err != nil {
				return err
			}
		}
		if err := s.packetWriter.WriteRTP(pkt); err != nil {
			return err
		}
	}
	if s.packetWriter != nil {
		err := s.packetWriter.Close()
		s.packetWriter = nil
		return err
	}
	return nil
}

func (s *archiveVideoSegmenter) rotateLocked() error {
	if s.packetWriter != nil {
		if err := s.packetWriter.Close(); err != nil {
			return err
		}
		s.packetWriter = nil
	}
	path := fmt.Sprintf(s.outputPattern, s.segmentIndex)
	s.segmentIndex++
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return err
	}
	var err error
	switch s.mimeType {
	case "video/vp8":
		s.packetWriter, err = newSequentialIVFWriter(
			path,
			"video/VP8",
			uint16(maxInt(1, s.width)),
			uint16(maxInt(1, s.height)),
			s.clockRate,
			s.requestKeyFrame,
		)
	case "video/vp9":
		s.packetWriter, err = newSequentialIVFWriter(
			path,
			"video/VP9",
			uint16(maxInt(1, s.width)),
			uint16(maxInt(1, s.height)),
			s.clockRate,
			s.requestKeyFrame,
		)
	case "video/h264":
		baseWriter, baseErr := h264writer.New(path)
		if baseErr == nil {
			s.packetWriter = &h264PacketWriter{writer: baseWriter}
		}
		err = baseErr
	default:
		err = fmt.Errorf("unsupported archive mime: %s", s.mimeType)
	}
	if err != nil {
		return err
	}
	s.segmentStarted = time.Now()
	s.rotatePending = false
	if s.requestKeyFrame != nil {
		s.requestKeyFrame()
	}
	return nil
}

func (s *archiveVideoSegmenter) isKeyframePacket(pkt *rtp.Packet) bool {
	if pkt == nil {
		return false
	}
	switch s.mimeType {
	case "video/vp8":
		var dep codecs.VP8Packet
		payload, err := dep.Unmarshal(pkt.Payload)
		if err != nil {
			return false
		}
		if dep.S == 0 || dep.PID != 0 || len(payload) == 0 {
			return false
		}
		return payload[0]&0x01 == 0
	case "video/h264":
		if len(pkt.Payload) == 0 {
			return false
		}
		nalType := pkt.Payload[0] & 0x1F
		switch nalType {
		case 5:
			return true
		case 24:
			payload := pkt.Payload[1:]
			for len(payload) > 2 {
				size := int(payload[0])<<8 | int(payload[1])
				payload = payload[2:]
				if size <= 0 || len(payload) < size {
					break
				}
				if payload[0]&0x1F == 5 {
					return true
				}
				payload = payload[size:]
			}
		}
		return false
	default:
		return false
	}
}

func videoEncodeChain(bitrateKbps int, keyIntMax int) []string {
	return []string{
		"!",
		"x264enc",
		"tune=zerolatency",
		"speed-preset=veryfast",
		fmt.Sprintf("bitrate=%d", bitrateKbps),
		fmt.Sprintf("key-int-max=%d", keyIntMax),
		"bframes=0",
		"byte-stream=false",
		"threads=0",
		"!",
		"video/x-h264,profile=main",
		"!",
		"h264parse",
		"config-interval=1",
	}
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func maxUint32(a, b uint32) uint32 {
	if a > b {
		return a
	}
	return b
}

func isRetryableLocalUDPError(err error) bool {
	if err == nil {
		return false
	}
	text := strings.ToLower(err.Error())
	return strings.Contains(text, "connection refused") || strings.Contains(text, "no buffer space")
}

func nowISO() string {
	return time.Now().Format(time.RFC3339)
}

var _ = rtcp.PictureLossIndication{}
var _ = rtp.Packet{}
var _ = livekit.ParticipantInfo{}
