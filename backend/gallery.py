"""Archive originals before cleanup and retain complete generation metadata."""
import asyncio
import io
import json
import math
import mimetypes
import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from PIL import Image

from .database import gallery_summary, resolve_record_project

UNASSIGNED_PROJECT = '__none__'
MEDIA_SUFFIXES = {'.png', '.jpg', '.jpeg', '.webp', '.mp4', '.webm'}
VIDEO_SUFFIXES = {'.mp4', '.webm'}
LOCAL_CACHE_MAX_BYTES = 256 * 1024 * 1024
PREVIEW_CACHE_MAX_BYTES = 512 * 1024 * 1024


def normalize_project_name(value):
    if value is None:
        return ''
    name = str(value).strip()
    if not name:
        return ''
    if name in {UNASSIGNED_PROJECT, '.', '..'} or '/' in name or '\\' in name:
        raise ValueError('项目名称不能包含斜杠或保留字符')
    if len(name) > 80 or any(ord(ch) < 32 for ch in name):
        raise ValueError('项目名称长度须在 80 字以内且不能包含控制字符')
    return name


def record_project(record):
    try:
        return normalize_project_name(resolve_record_project(record))
    except ValueError:
        return ''


class Gallery:
    def __init__(self, db, drive, data_dir, settings=None):
        self.db, self.drive = db, drive
        self.settings = settings or (lambda: {})
        self.root = Path(data_dir)
        (self.root / 'images').mkdir(parents=True, exist_ok=True)
        (self.root / 'thumbnails').mkdir(parents=True, exist_ok=True)
        (self.root / 'previews').mkdir(parents=True, exist_ok=True)
        self._file_locks = {}
        self._trim_task = None
        self._preview_trim_task = None

    def _lock_for(self, image_id):
        key = str(image_id)
        lock = self._file_locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._file_locks[key] = lock
        return lock

    def should_prune_local(self):
        cfg = self.settings() if callable(self.settings) else (self.settings or {})
        val = (cfg or {}).get('prune_local_after_archive', True)
        return True if val is None else bool(val)

    def _ensure_project_record(self, name, *, drive_folder_id=None):
        if not name:
            return None
        existing = self.db.get('projects', name) or {}
        payload = {'id': name, 'name': name}
        if drive_folder_id:
            payload['drive_folder_id'] = drive_folder_id
        elif existing.get('drive_folder_id'):
            payload['drive_folder_id'] = existing['drive_folder_id']
        return self.db.save('projects', payload)

    @staticmethod
    def _lanczos():
        return getattr(getattr(Image, 'Resampling', Image), 'LANCZOS', Image.BICUBIC)

    @staticmethod
    def _webp_mode_image(image, *, preserve_alpha=False):
        if preserve_alpha and (
            image.mode in ('RGBA', 'LA')
            or (image.mode == 'P' and 'transparency' in getattr(image, 'info', {}))
        ):
            return image.convert('RGBA')
        return image.convert('RGB')

    @staticmethod
    def image_preview(source, thumbnail):
        source, thumbnail = Path(source), Path(thumbnail)
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        tmp = thumbnail.with_name(f'.{thumbnail.name}.{uuid.uuid4().hex[:8]}.tmp')
        try:
            with Image.open(source) as image:
                width, height = image.size
                preview = image.convert('RGB')
                preview.thumbnail((512, 768))
                preview.save(tmp, 'WEBP', quality=85)
            tmp.replace(thumbnail)
            return width, height
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    @classmethod
    def image_hd_preview(cls, source, preview_path, max_dim=1600, quality=88):
        source, preview_path = Path(source), Path(preview_path)
        preview_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = preview_path.with_name(f'.{preview_path.name}.{uuid.uuid4().hex[:8]}.tmp')
        try:
            with Image.open(source) as image:
                width, height = image.size
                hd = cls._webp_mode_image(image, preserve_alpha=True)
                hd.thumbnail((max_dim, max_dim), cls._lanczos())
                hd.save(tmp, 'WEBP', quality=quality)
            tmp.replace(preview_path)
            return width, height
        except Exception:
            tmp.unlink(missing_ok=True)
            raise

    @classmethod
    def image_derivatives(cls, source_bytes, thumbnail, preview, *, max_dim=1600, preview_quality=88):
        thumbnail, preview = Path(thumbnail), Path(preview)
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        preview.parent.mkdir(parents=True, exist_ok=True)
        tmp_thumb = thumbnail.with_name(f'.{thumbnail.name}.{uuid.uuid4().hex[:8]}.tmp')
        tmp_prev = preview.with_name(f'.{preview.name}.{uuid.uuid4().hex[:8]}.tmp')
        try:
            with Image.open(io.BytesIO(source_bytes)) as image:
                width, height = image.size
                thumb_img = image.convert('RGB')
                thumb_img.thumbnail((512, 768))
                thumb_img.save(tmp_thumb, 'WEBP', quality=85)

                hd_img = cls._webp_mode_image(image, preserve_alpha=True)
                hd_img.thumbnail((max_dim, max_dim), cls._lanczos())
                hd_img.save(tmp_prev, 'WEBP', quality=preview_quality)
            tmp_thumb.replace(thumbnail)
            tmp_prev.replace(preview)
            return width, height
        except Exception:
            tmp_thumb.unlink(missing_ok=True)
            tmp_prev.unlink(missing_ok=True)
            raise

    async def archive(self, task_id, content, filename, params, parent_id=None, project=None):
        image_id = uuid.uuid4().hex
        suffix = Path(filename).suffix.lower()
        video = suffix in VIDEO_SUFFIXES
        if suffix not in MEDIA_SUFFIXES:
            suffix = '.png'
        params_copy = dict(params or {})
        raw_project = project if project is not None else (params_copy.get('project') if params_copy.get('project') is not None else params_copy.get('album'))
        project_name = normalize_project_name(raw_project)
        if project_name:
            params_copy['project'] = project_name
        else:
            params_copy.pop('project', None)
        params_copy.pop('album', None)
        if project_name:
            self._ensure_project_record(project_name)

        destination = self.root / 'images' / (image_id + suffix)
        thumbnail = self.root / 'thumbnails' / (image_id + '.webp')
        preview = self.root / 'previews' / (image_id + '.webp')
        await asyncio.to_thread(destination.write_bytes, content)
        duration = None
        try:
            if video:
                await asyncio.to_thread(self.conform_output_fps, destination, params_copy)
                width, height, duration = await asyncio.to_thread(self.video_preview, destination, thumbnail)
            else:
                width, height = await asyncio.to_thread(self.image_derivatives, content, thumbnail, preview)
        except Exception:
            destination.unlink(missing_ok=True)
            thumbnail.unlink(missing_ok=True)
            preview.unlink(missing_ok=True)
            raise
        record = self.db.save('gallery', {
            'id': image_id, 'task_id': task_id, 'filename': destination.name,
            'local_path': str(destination), 'local_cached': True,
            'width': width, 'height': height,
            'media_type': 'video' if video else 'image',
            'mime_type': mimetypes.guess_type(destination.name)[0] or 'application/octet-stream',
            'duration': duration,
            'params': params_copy, 'parent_id': parent_id, 'favorite': False,
            'tags': [], 'project': project_name, 'album': project_name,
            'drive_file_id': None, 'drive_parent_id': None, 'archive_status': 'pending',
            'url': '/api/gallery/' + image_id + '/download',
            'thumbnail_url': '/api/gallery/' + image_id + '/thumbnail',
            'preview_url': '/api/gallery/' + image_id + '/preview',
        })
        if not video:
            self._schedule_preview_trim(exclude_id=image_id)
        # Background Drive upload so task completion is instantaneous for user
        asyncio.create_task(self.retry_archive(record))
        return record

    @staticmethod
    def conform_output_fps(source, params):
        """60fps 补帧在图里是 3×/72fps，这里抽到正好 60fps，音频时长不变。"""
        from .workflows import parse_frame_interp, parse_video_crf
        if parse_frame_interp((params or {}).get("frame_interp")) != 60:
            return
        crf = parse_video_crf((params or {}).get("video_crf"), default=15)
        source = Path(source)
        tmp = source.with_name(source.stem + ".fps60" + source.suffix)
        try:
            subprocess.run(
                ["ffmpeg", "-v", "error", "-nostdin", "-y", "-i", str(source),
                 "-filter:v", "fps=60", "-c:v", "libx264", "-crf", str(crf), "-preset", "medium",
                 "-pix_fmt", "yuv420p", "-c:a", "copy", "-movflags", "+faststart", str(tmp)],
                capture_output=True, timeout=300, check=True,
            )
            tmp.replace(source)
        except FileNotFoundError as exc:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("视频补帧转 60fps 需要 ffmpeg") from exc
        except subprocess.SubprocessError as exc:
            tmp.unlink(missing_ok=True)
            raise RuntimeError("视频未能转到 60fps") from exc

    @staticmethod
    def extract_frame(source, destination, position='last'):
        """Extract the first or last frame of a video as PNG for H3 continuation/keyframe chaining."""
        source, destination = Path(source), Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if position not in ('first', 'last'):
            raise ValueError('position 只支持 first 或 last')
        try:
            if position == 'first':
                subprocess.run(
                    ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source), '-frames:v', '1', str(destination)],
                    capture_output=True, timeout=60, check=True,
                )
            else:
                res = subprocess.run(
                    ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-sseof', '-0.5', '-i', str(source),
                     '-update', '1', '-q:v', '1', str(destination)],
                    capture_output=True, timeout=60,
                )
                if res.returncode != 0 or not destination.is_file() or destination.stat().st_size == 0:
                    subprocess.run(
                        ['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source),
                         '-update', '1', '-q:v', '1', str(destination)],
                        capture_output=True, timeout=120, check=True,
                    )
            if not destination.is_file() or destination.stat().st_size == 0:
                raise RuntimeError('未能从视频中提取到有效帧')
            return destination
        except FileNotFoundError as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError('抽取视频帧需要 ffmpeg，请安装后重试') from exc
        except subprocess.SubprocessError as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError('视频抽帧失败，请检查源文件') from exc

    @staticmethod
    def _probe_video(source):
        probe = subprocess.run(
            ['ffprobe', '-v', 'error', '-show_entries',
             'stream=codec_type,width,height,r_frame_rate:format=duration', '-of', 'json', str(source)],
            capture_output=True, text=True, timeout=30, check=True,
        )
        info = json.loads(probe.stdout)
        streams = info.get('streams', [])
        video = next((s for s in streams if s.get('codec_type') == 'video'), None)
        if not video:
            raise ValueError(f'文件不含视频流: {source}')
        has_audio = any(s.get('codec_type') == 'audio' for s in streams)
        rate = str(video.get('r_frame_rate') or '24/1')
        if '/' in rate:
            num, den = rate.split('/', 1)
            fps = float(num) / float(den) if float(den) else 24.0
        else:
            fps = float(rate)
        if fps <= 0 or fps > 240:
            fps = 24.0
        return int(video['width']), int(video['height']), fps, has_audio

    @classmethod
    def concat_videos(cls, sources, destination, trim_overlap_frames=0, crf=15):
        """Concatenate H3 segments seamlessly; trim_overlap_frames uses H3's 24fps time base across 24/48/60fps clips."""
        from .workflows import parse_video_crf
        crf = parse_video_crf(crf, default=15)
        paths = [Path(s) for s in sources]
        if len(paths) < 2:
            raise ValueError('至少需要选择 2 段视频才能拼接')
        trim = int(trim_overlap_frames or 0)
        if trim < 0 or trim > 240:
            raise ValueError('重叠裁剪帧数须在 0 到 240 之间')
        overlap_seconds = trim / 24.0
        destination = Path(destination)
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            metas = [cls._probe_video(p) for p in paths]
            w, h, fps, _ = metas[0]
            all_audio = all(m[3] for m in metas)
            cmd = ['ffmpeg', '-v', 'error', '-nostdin', '-y']
            for p in paths:
                cmd += ['-i', str(p)]
            filters = []
            concat_inputs = []
            for idx in range(len(paths)):
                seg_fps = metas[idx][2]
                seg_trim_frames = int(round(overlap_seconds * seg_fps)) if (idx > 0 and trim > 0) else 0
                vchain = []
                if seg_trim_frames > 0:
                    vchain.append(f'trim=start_frame={seg_trim_frames}')
                vchain += [
                    'setpts=PTS-STARTPTS',
                    f'scale={w}:{h}:force_original_aspect_ratio=decrease',
                    f'pad={w}:{h}:(ow-iw)/2:(oh-ih)/2',
                    'setsar=1',
                    f'fps={fps:.6g}',
                    'format=yuv420p',
                ]
                filters.append(f'[{idx}:v]' + ','.join(vchain) + f'[v{idx}]')
                concat_inputs.append(f'[v{idx}]')
                if all_audio:
                    achain = []
                    if idx > 0 and trim > 0:
                        achain.append(f'atrim=start={overlap_seconds:.6f}')
                    achain.append('asetpts=PTS-STARTPTS')
                    filters.append(f'[{idx}:a]' + ','.join(achain) + f'[a{idx}]')
                    concat_inputs.append(f'[a{idx}]')
            if all_audio:
                filters.append(''.join(concat_inputs) + f'concat=n={len(paths)}:v=1:a=1[vout][aout]')
                maps = ['-map', '[vout]', '-map', '[aout]', '-c:a', 'aac', '-b:a', '192k']
            else:
                filters.append(''.join(concat_inputs) + f'concat=n={len(paths)}:v=1:a=0[vout]')
                maps = ['-map', '[vout]']
            cmd += ['-filter_complex', ';'.join(filters)] + maps + [
                '-c:v', 'libx264', '-crf', str(crf), '-preset', 'medium', '-pix_fmt', 'yuv420p', '-movflags', '+faststart', str(destination),
            ]
            subprocess.run(cmd, capture_output=True, timeout=600, check=True)
            return destination
        except FileNotFoundError as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError('拼接视频需要 ffmpeg 与 ffprobe，请安装后重试') from exc
        except (subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
            destination.unlink(missing_ok=True)
            raise RuntimeError(f'视频拼接失败: {exc}') from exc

    @staticmethod
    def video_preview(source, thumbnail):
        """Probe and thumbnail locally archived video without blocking the queue loop."""
        source, thumbnail = Path(source), Path(thumbnail)
        thumbnail.parent.mkdir(parents=True, exist_ok=True)
        tmp = thumbnail.with_name(f'{thumbnail.stem}.{uuid.uuid4().hex[:8]}.tmp.webp')
        try:
            probe = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                                    '-show_entries', 'stream=width,height:format=duration', '-of', 'json', str(source)],
                                   capture_output=True, text=True, timeout=30, check=True)
            info = json.loads(probe.stdout)
            stream = info['streams'][0]
            subprocess.run(['ffmpeg', '-v', 'error', '-nostdin', '-y', '-i', str(source),
                            '-frames:v', '1', '-vf', "scale=w='min(512,iw)':h='min(768,ih)':force_original_aspect_ratio=decrease",
                            str(tmp)], capture_output=True, timeout=60, check=True)
            tmp.replace(thumbnail)
            return int(stream['width']), int(stream['height']), float(info.get('format', {}).get('duration') or 0)
        except FileNotFoundError as exc:
            tmp.unlink(missing_ok=True)
            raise RuntimeError('视频归档需要 ffmpeg 与 ffprobe，请安装后重试') from exc
        except (subprocess.SubprocessError, ValueError, KeyError, IndexError) as exc:
            tmp.unlink(missing_ok=True)
            raise RuntimeError('视频读取或缩略图生成失败，请检查输出文件') from exc

    async def _drive_parent_for(self, row, project_name):
        if project_name:
            parent = await self.drive.project_folder(project_name, create=True)
            self._ensure_project_record(project_name, drive_folder_id=parent)
            return parent
        folders = await self.drive.folders()
        folder_kind = 'upscaled' if row.get('parent_id') else 'originals'
        return folders.get(folder_kind) or folders.get('outputs')

    async def retry_archive(self, record):
        latest = self.db.get('gallery', record['id']) or record
        try:
            if not self.drive:
                raise RuntimeError('Drive 未初始化')
            project_name = record_project(latest)
            parent = await self._drive_parent_for(latest, project_name)
            if not parent:
                raise RuntimeError('Drive 输出目录未配置')
            file_id = latest.get('drive_file_id')
            if not file_id:
                result = await self.drive.upload(latest['local_path'], parent, latest['filename'])
                file_id = result.get('id') if isinstance(result, dict) else result
            if not file_id:
                raise RuntimeError('Drive 上传未返回文件 ID')
            self.db.save('gallery', {'id': latest['id'], 'drive_file_id': file_id, 'drive_parent_id': parent})
            metadata_path = self.root / 'images' / (latest['id'] + '.json')
            metadata_payload = {
                **latest,
                'project': project_name,
                'album': project_name,
                'drive_file_id': file_id,
                'drive_parent_id': parent,
            }
            metadata_path.write_text(json.dumps(metadata_payload, ensure_ascii=False, indent=2))
            metadata_id = latest.get('metadata_file_id')
            if not metadata_id:
                metadata_result = await self.drive.upload(str(metadata_path), parent, latest['id'] + '.json')
                metadata_id = metadata_result.get('id') if isinstance(metadata_result, dict) else metadata_result
            if not metadata_id:
                raise RuntimeError('Drive 参数元数据上传未返回文件 ID')
            saved = self.db.save('gallery', {
                'id': latest['id'],
                'project': project_name,
                'album': project_name,
                'drive_file_id': file_id,
                'metadata_file_id': metadata_id,
                'drive_parent_id': parent,
                'archive_status': 'saved',
                'archive_error': None,
            })
            thumb = self.root / 'thumbnails' / (latest['id'] + '.webp')
            if self.should_prune_local() and thumb.is_file() and file_id and metadata_id:
                local_file = Path(latest['local_path']) if latest.get('local_path') else None
                if local_file:
                    local_file.unlink(missing_ok=True)
                metadata_path.unlink(missing_ok=True)
                saved = self.db.save('gallery', {'id': latest['id'], 'local_cached': False})
            return saved
        except Exception as exc:
            return self.db.save('gallery', {'id': latest['id'], 'archive_status': 'retry', 'archive_error': str(exc)})

    def trim_local_cache(self, max_bytes=LOCAL_CACHE_MAX_BYTES, exclude_id=None):
        images_dir = self.root / 'images'
        if not images_dir.is_dir():
            return 0
        entries = []
        total_bytes = 0
        for path in images_dir.iterdir():
            if path.is_symlink() or not path.is_file() or path.suffix.lower() in {'.json', '.part'}:
                continue
            image_id = path.stem
            row = self.db.get('gallery', image_id)
            if not row or row.get('archive_status') != 'saved' or not row.get('drive_file_id'):
                continue
            thumb = self.root / 'thumbnails' / (image_id + '.webp')
            if not thumb.is_file():
                continue
            try:
                st = path.stat()
            except OSError:
                continue
            total_bytes += st.st_size
            if exclude_id and image_id == str(exclude_id):
                continue
            entries.append((st.st_mtime, st.st_size, path, image_id))
        if total_bytes <= max_bytes:
            return 0
        entries.sort(key=lambda item: item[0])
        removed = 0
        for _, size, path, image_id in entries:
            if total_bytes <= max_bytes:
                break
            path.unlink(missing_ok=True)
            (images_dir / (image_id + '.json')).unlink(missing_ok=True)
            self.db.save('gallery', {'id': image_id, 'local_cached': False})
            total_bytes -= size
            removed += 1
        return removed

    def trim_preview_cache(self, max_bytes=PREVIEW_CACHE_MAX_BYTES, exclude_id=None):
        previews_dir = self.root / 'previews'
        if not previews_dir.is_dir():
            return 0
        entries = []
        total_bytes = 0
        for path in previews_dir.iterdir():
            if path.is_symlink() or not path.is_file() or path.name.startswith('.') or path.suffix.lower() != '.webp':
                continue
            image_id = path.stem
            try:
                st = path.stat()
            except OSError:
                continue
            total_bytes += st.st_size
            if exclude_id and image_id == str(exclude_id):
                continue
            entries.append((st.st_mtime, st.st_size, path))
        if total_bytes <= max_bytes:
            return 0
        entries.sort(key=lambda item: item[0])
        removed = 0
        for _, size, path in entries:
            if total_bytes <= max_bytes:
                break
            try:
                path.unlink(missing_ok=True)
            except OSError:
                continue
            total_bytes -= size
            removed += 1
        return removed

    def _schedule_trim(self, exclude_id=None):
        if self._trim_task and not self._trim_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        async def _run():
            try:
                await asyncio.to_thread(self.trim_local_cache, LOCAL_CACHE_MAX_BYTES, exclude_id)
            except Exception:
                pass
        self._trim_task = loop.create_task(_run())

    def _schedule_preview_trim(self, exclude_id=None):
        if self._preview_trim_task and not self._preview_trim_task.done():
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return
        async def _run():
            try:
                await asyncio.to_thread(self.trim_preview_cache, PREVIEW_CACHE_MAX_BYTES, exclude_id)
            except Exception:
                pass
        self._preview_trim_task = loop.create_task(_run())

    async def close(self):
        for task in (self._trim_task, self._preview_trim_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _ensure_local_unlocked(self, row, *, trim=True):
        path = Path(row['local_path'])
        downloaded = False
        if not path.is_file() and row.get('drive_file_id') and self.drive:
            await self.drive.download(row['drive_file_id'], path)
            downloaded = True
        if not path.is_file():
            raise FileNotFoundError('原图不可用')
        try:
            if downloaded or (time.time() - path.stat().st_mtime) > 60:
                os.utime(path, None)
        except OSError:
            pass
        if row.get('local_cached') is False:
            self.db.save('gallery', {'id': row['id'], 'local_cached': True})
        if trim and downloaded and self.should_prune_local():
            self._schedule_trim(exclude_id=row['id'])
        return path

    async def ensure_local_file(self, row, *, trim=True):
        async with self._lock_for(row['id']):
            return await self._ensure_local_unlocked(row, trim=trim)

    async def ensure_preview(self, row):
        image_id = row['id']
        preview = self.root / 'previews' / (image_id + '.webp')
        if preview.is_file():
            try:
                if (time.time() - preview.stat().st_mtime) > 60:
                    os.utime(preview, None)
            except OSError:
                pass
            return preview
        async with self._lock_for(image_id):
            if preview.is_file():
                return preview
            had_local = Path(row['local_path']).is_file()
            source = await self._ensure_local_unlocked(row, trim=False)
            thumb = self.root / 'thumbnails' / (image_id + '.webp')
            updates = {'id': image_id}
            w = None
            h = None
            try:
                w, h = await asyncio.to_thread(self.image_hd_preview, source, preview)
                if not thumb.is_file():
                    await asyncio.to_thread(self.image_preview, source, thumb)
                if not row.get('width') and w:
                    updates.update(width=w, height=h)
            except Exception:
                pass
            if len(updates) > 1:
                self.db.save('gallery', updates)
            if not had_local and self.should_prune_local():
                self._schedule_trim(exclude_id=image_id)
            if preview.is_file():
                self._schedule_preview_trim(exclude_id=image_id)
        return preview if preview.is_file() else None

    async def ensure_thumbnail(self, row):
        image_id = row['id']
        thumb = self.root / 'thumbnails' / (image_id + '.webp')
        if thumb.is_file():
            return thumb
        async with self._lock_for(image_id):
            if thumb.is_file():
                return thumb
            local_path = Path(row['local_path'])
            had_local = local_path.is_file()
            source = await self._ensure_local_unlocked(row, trim=False)
            updates = {'id': image_id}
            try:
                if row.get('media_type') == 'video':
                    w, h, dur = await asyncio.to_thread(self.video_preview, source, thumb)
                    if not row.get('width'):
                        updates.update(width=w, height=h, duration=dur)
                else:
                    w, h = await asyncio.to_thread(self.image_preview, source, thumb)
                    if not row.get('width'):
                        updates.update(width=w, height=h)
            except Exception:
                pass
            if thumb.is_file() and not had_local and self.should_prune_local() and row.get('archive_status') == 'saved' and row.get('drive_file_id'):
                Path(source).unlink(missing_ok=True)
                (self.root / 'images' / (image_id + '.json')).unlink(missing_ok=True)
                updates['local_cached'] = False
            if len(updates) > 1:
                self.db.save('gallery', updates)
        return thumb if thumb.is_file() else None

    async def prune_local_originals(self):
        rows = self.db.list('gallery')
        pruned = 0
        freed_bytes = 0
        skipped = 0
        for row in rows:
            if row.get('archive_status') != 'saved' or not row.get('drive_file_id'):
                continue
            local_path = Path(row['local_path']) if row.get('local_path') else None
            meta_path = self.root / 'images' / (row['id'] + '.json')
            thumb = self.root / 'thumbnails' / (row['id'] + '.webp')
            if local_path and local_path.is_file():
                if not thumb.is_file():
                    try:
                        if row.get('media_type') == 'video':
                            await asyncio.to_thread(self.video_preview, local_path, thumb)
                        else:
                            await asyncio.to_thread(self.image_preview, local_path, thumb)
                    except Exception:
                        skipped += 1
                        continue
                if not thumb.is_file():
                    skipped += 1
                    continue
                size = 0
                try:
                    size += local_path.stat().st_size
                except OSError:
                    pass
                if meta_path.is_file():
                    try:
                        size += meta_path.stat().st_size
                    except OSError:
                        pass
                local_path.unlink(missing_ok=True)
                meta_path.unlink(missing_ok=True)
                self.db.save('gallery', {'id': row['id'], 'local_cached': False})
                pruned += 1
                freed_bytes += size
            elif meta_path.is_file():
                try:
                    freed_bytes += meta_path.stat().st_size
                except OSError:
                    pass
                meta_path.unlink(missing_ok=True)
                if row.get('local_cached') is not False:
                    self.db.save('gallery', {'id': row['id'], 'local_cached': False})
            elif row.get('local_cached') is not False and thumb.is_file():
                self.db.save('gallery', {'id': row['id'], 'local_cached': False})
        return {'pruned': pruned, 'freed_bytes': freed_bytes, 'skipped': skipped}

    def filtered(
        self,
        q='',
        favorite=False,
        album='',
        model='',
        date='',
        tag='',
        order='desc',
        models=None,
        *,
        project='',
        media_type='',
        lora='',
        page=None,
        limit=None,
        paginated=False,
        include_workflow=False,
    ):
        effective_project = UNASSIGNED_PROJECT if project == UNASSIGNED_PROJECT else (normalize_project_name(project) if project else normalize_project_name(album))
        target_ids = None
        if model:
            target_ids = {model}
            if models:
                m_obj = next((m for m in models if model in (m.get('id'), m.get('name'), m.get('filename'), m.get('comfy_name'))), None)
                if m_obj:
                    target_ids.update(filter(None, [m_obj.get('id'), m_obj.get('name'), m_obj.get('filename'), m_obj.get('comfy_name')]))

        use_pagination = paginated or page is not None or limit is not None
        page_num = max(1, int(page or 1)) if use_pagination else None
        page_size = max(1, min(200, int(limit or 36))) if use_pagination else None

        if hasattr(self.db, 'query_gallery'):
            res = self.db.query_gallery(
                q=q,
                favorite=favorite,
                project=effective_project,
                media_type=media_type,
                model_ids=target_ids,
                lora=lora,
                date=date,
                tag=tag,
                order=order,
                page=page_num,
                limit=page_size,
                include_workflow=include_workflow,
            )
            items, total = res['items'], res['total']
        else:
            result = self.db.list('gallery')
            if favorite:
                result = [r for r in result if r.get('favorite')]
            if effective_project == UNASSIGNED_PROJECT:
                result = [r for r in result if not record_project(r)]
            elif effective_project:
                result = [r for r in result if record_project(r) == effective_project]
            if media_type:
                result = [r for r in result if (r.get('media_type') or 'image') == media_type]
            if target_ids:
                def matches_model(r):
                    p = r.get('params', {}) or {}
                    if p.get('model') in target_ids:
                        return True
                    for s in p.get('model_snapshot', []) or []:
                        if any(s.get(k) in target_ids for k in ('id', 'name', 'filename', 'comfy_name')):
                            return True
                    return False
                result = [r for r in result if matches_model(r)]
            if lora:
                result = [r for r in result if any(l.get('id') == lora or l.get('name') == lora for l in ((r.get('params') or {}).get('loras') or []))]
            if date:
                result = [r for r in result if r.get('created_at', '').startswith(date)]
            if tag:
                result = [r for r in result if tag in r.get('tags', [])]
            if q:
                q_low = q.lower()
                result = [r for r in result if q_low in json.dumps(r, ensure_ascii=False).lower()]
            result.sort(key=lambda r: r.get('created_at', ''), reverse=(order != 'asc'))
            total = len(result)
            if use_pagination:
                start = (page_num - 1) * page_size
                sliced = result[start:start + page_size]
            else:
                sliced = result
            items = sliced if include_workflow else [gallery_summary(r) for r in sliced]

        if use_pagination:
            total_pages = max(1, math.ceil(total / page_size))
            return {
                'items': items,
                'total': total,
                'page': page_num,
                'limit': page_size,
                'total_pages': total_pages,
                'has_more': page_num < total_pages,
            }
        return items

    def list_projects(self):
        if hasattr(self.db, 'gallery_project_stats'):
            stats = self.db.gallery_project_stats()
            by_project = stats['by_project']
            total = stats['total']
            unassigned_count = stats['unassigned_count']
        else:
            rows = self.db.list('gallery')
            total = len(rows)
            unassigned_count = 0
            by_project = {}
            for r in sorted(rows, key=lambda x: x.get('created_at', ''), reverse=True):
                proj = record_project(r)
                if not proj:
                    unassigned_count += 1
                    continue
                entry = by_project.setdefault(proj, {'name': proj, 'count': 0, 'latest_at': r.get('created_at') or '', 'cover_thumbnail_url': r.get('thumbnail_url')})
                entry['count'] += 1
                if (r.get('created_at') or '') > entry['latest_at']:
                    entry['latest_at'] = r.get('created_at') or ''
                    entry['cover_thumbnail_url'] = r.get('thumbnail_url')

        saved_projects = {p.get('name') or p.get('id'): p for p in self.db.list('projects') if (p.get('name') or p.get('id'))}
        projects = []
        all_names = sorted(set(by_project.keys()) | set(saved_projects.keys()), key=lambda n: (-(1 if by_project.get(n, {}).get('count') else 0), by_project.get(n, {}).get('latest_at') or saved_projects.get(n, {}).get('updated_at') or ''), reverse=False)
        for name in all_names:
            stat = by_project.get(name) or {}
            saved = saved_projects.get(name) or {}
            projects.append({
                'name': name,
                'count': int(stat.get('count') or 0),
                'latest_at': stat.get('latest_at') or saved.get('updated_at') or saved.get('created_at') or '',
                'cover_thumbnail_url': stat.get('cover_thumbnail_url'),
                'drive_folder_id': saved.get('drive_folder_id'),
            })
        projects.sort(key=lambda item: (item['latest_at'], item['name'].lower()), reverse=True)
        return {'projects': projects, 'total': total, 'unassigned_count': unassigned_count}

    async def create_project(self, name):
        name = normalize_project_name(name)
        if not name:
            raise ValueError('项目名称不能为空')
        folder_id = None
        if self.drive and getattr(self.drive, 'is_configured', lambda: True)():
            folder_id = await self.drive.project_folder(name, create=True)
        saved = self._ensure_project_record(name, drive_folder_id=folder_id)
        info = next((p for p in self.list_projects()['projects'] if p['name'] == name), None)
        return info or {'name': name, 'count': 0, 'latest_at': saved.get('updated_at') or '', 'cover_thumbnail_url': None, 'drive_folder_id': folder_id}

    async def rename_project(self, old_name, new_name):
        old_clean = normalize_project_name(old_name)
        new_clean = normalize_project_name(new_name)
        if not old_clean or not new_clean:
            raise ValueError('项目名称不能为空')
        if old_clean == new_clean:
            info = next((p for p in self.list_projects()['projects'] if p['name'] == old_clean), None)
            if not info:
                raise KeyError('项目不存在')
            return info
        all_rows = self.db.list('gallery')
        old_rows = [r for r in all_rows if record_project(r) == old_clean]
        old_rec = self.db.get('projects', old_clean)
        if not old_rec and not old_rows:
            raise KeyError('项目不存在')
        new_rows = [r for r in all_rows if record_project(r) == new_clean]
        if self.db.get('projects', new_clean) or new_rows:
            raise ValueError(f'项目“{new_clean}”已存在')

        folder_id = (old_rec or {}).get('drive_folder_id')
        if self.drive and getattr(self.drive, 'is_configured', lambda: True)():
            if not folder_id:
                folder_id = await self.drive.project_folder(old_clean, create=False)
            if folder_id:
                await self.drive.rename_file(folder_id, new_clean)
                if hasattr(self.drive, '_project_folders'):
                    self.drive._project_folders.pop(old_clean, None)
                    self.drive._project_folders[new_clean] = folder_id

        self._ensure_project_record(new_clean, drive_folder_id=folder_id)
        self.db.delete('projects', old_clean)
        for row in old_rows:
            params = dict(row.get('params') or {})
            params['project'] = new_clean
            params.pop('album', None)
            self.db.save('gallery', {'id': row['id'], 'project': new_clean, 'album': new_clean, 'params': params})
        return next((p for p in self.list_projects()['projects'] if p['name'] == new_clean), {'name': new_clean, 'count': len(old_rows), 'drive_folder_id': folder_id})

    async def delete_project(self, name, *, unassign=False):
        clean = normalize_project_name(name)
        if not clean:
            raise ValueError('项目名称不能为空')
        proj_rec = self.db.get('projects', clean)
        items = [r for r in self.db.list('gallery') if record_project(r) == clean]
        if not proj_rec and not items:
            raise KeyError('项目不存在')
        if items and not unassign:
            raise ValueError(f'项目“{clean}”下仍有 {len(items)} 个作品，请先移动作品或勾选移入未分类后再删除')
        for row in items:
            await self.move_to_project(row['id'], '')
        if self.drive and getattr(self.drive, 'is_configured', lambda: True)():
            folder_id = (proj_rec or {}).get('drive_folder_id') or await self.drive.project_folder(clean, create=False)
            if folder_id and not await self.drive.folder_has_children(folder_id):
                await self.drive.trash(folder_id)
                if hasattr(self.drive, '_project_folders'):
                    self.drive._project_folders.pop(clean, None)
        self.db.delete('projects', clean)
        return {'ok': True, 'unassigned': len(items)}

    async def move_to_project(self, image_id, project):
        row = self.db.get('gallery', image_id)
        if not row:
            raise KeyError('作品不存在')
        target = normalize_project_name(project)
        if target:
            self._ensure_project_record(target)
        updates = {'id': image_id, 'project': target, 'album': target}
        params = dict(row.get('params') or {})
        if target:
            params['project'] = target
        else:
            params.pop('project', None)
        params.pop('album', None)
        updates['params'] = params

        if row.get('drive_file_id') and self.drive and getattr(self.drive, 'is_configured', lambda: True)():
            target_parent = await self._drive_parent_for(row, target)
            await self.drive.move_file(row['drive_file_id'], target_parent)
            if row.get('metadata_file_id'):
                try:
                    await self.drive.move_file(row['metadata_file_id'], target_parent)
                except Exception:
                    pass
            updates['drive_parent_id'] = target_parent
        return self.db.save('gallery', updates)

    async def sync_from_drive(self, project=None):
        if not self.drive or not getattr(self.drive, 'is_configured', lambda: True)():
            raise RuntimeError('Google Drive 未配置或未授权，无法同步')
        folders = await self.drive.folders()
        project_folders = await self.drive.list_project_folders()
        folder_map = {}
        for pf in project_folders:
            raw_name = pf.get('name') or ''
            try:
                pname = normalize_project_name(raw_name)
            except ValueError:
                continue
            if not pname:
                continue
            folder_map[pname] = pf['id']
            self._ensure_project_record(pname, drive_folder_id=pf['id'])

        targets = []
        if project == UNASSIGNED_PROJECT:
            targets = [('', folders['originals']), ('', folders['upscaled'])]
        elif project:
            pname = normalize_project_name(project)
            fid = folder_map.get(pname) or await self.drive.project_folder(pname, create=False)
            if fid:
                self._ensure_project_record(pname, drive_folder_id=fid)
                targets = [(pname, fid)]
        else:
            targets = [(pname, fid) for pname, fid in folder_map.items()]
            targets.append(('', folders['originals']))
            targets.append(('', folders['upscaled']))

        existing_rows = self.db.list('gallery')
        by_drive_id = {r['drive_file_id']: r for r in existing_rows if r.get('drive_file_id')}
        used_ids = {r['id'] for r in existing_rows}

        added = 0
        moved = 0
        skipped = 0
        for proj_name, folder_id in targets:
            if not folder_id:
                continue
            files = await self.drive.list_folder_files(folder_id, recursive=True)
            sidecars_by_parent = {}
            sidecars_by_stem = {}
            media_rows = []
            for f in files:
                name = str(f.get('name') or '')
                suffix = Path(name).suffix.lower()
                if suffix == '.json':
                    stem = Path(name).stem
                    for p in f.get('parents') or []:
                        sidecars_by_parent[(p, stem)] = f
                    sidecars_by_stem.setdefault(stem, f)
                elif suffix in MEDIA_SUFFIXES:
                    media_rows.append(f)

            for f in media_rows:
                fid = f.get('id')
                if not fid:
                    continue
                actual_parent = (f.get('parents') or [folder_id])[0]
                existing = by_drive_id.get(fid)
                if existing:
                    if record_project(existing) != proj_name or existing.get('drive_parent_id') != actual_parent:
                        params = dict(existing.get('params') or {})
                        if proj_name:
                            params['project'] = proj_name
                        else:
                            params.pop('project', None)
                        params.pop('album', None)
                        updated = self.db.save('gallery', {
                            'id': existing['id'],
                            'project': proj_name,
                            'album': proj_name,
                            'drive_parent_id': actual_parent,
                            'params': params,
                        })
                        by_drive_id[fid] = updated
                        moved += 1
                    else:
                        skipped += 1
                    continue

                name = str(f.get('name') or f'{fid}.png')
                suffix = Path(name).suffix.lower()
                stem = Path(name).stem
                sidecar = None
                for p in f.get('parents') or []:
                    if (p, stem) in sidecars_by_parent:
                        sidecar = sidecars_by_parent[(p, stem)]
                        break
                if not sidecar:
                    sidecar = sidecars_by_stem.get(stem)

                meta = {}
                if sidecar and sidecar.get('id'):
                    try:
                        text = await self.drive.download_text(sidecar['id'])
                        parsed = json.loads(text)
                        if isinstance(parsed, dict):
                            meta = parsed
                    except Exception:
                        meta = {}

                candidate_id = meta.get('id') or stem
                if re.fullmatch(r'[A-Za-z0-9_-]{8,64}', str(candidate_id)) and str(candidate_id) not in used_ids:
                    image_id = str(candidate_id)
                else:
                    image_id = uuid.uuid5(uuid.NAMESPACE_URL, f'drive:{fid}').hex
                    while image_id in used_ids:
                        image_id = uuid.uuid4().hex
                used_ids.add(image_id)

                raw_params = meta.get('params') if isinstance(meta.get('params'), dict) else (
                    meta if any(k in meta for k in ('prompt', 'model', 'workflow', 'workflow_id')) else {'source': 'drive_sync', 'prompt': ''}
                )
                params = dict(raw_params or {})
                if proj_name:
                    params['project'] = proj_name
                else:
                    params.pop('project', None)
                params.pop('album', None)

                video = suffix in VIDEO_SUFFIXES
                local_path = self.root / 'images' / (image_id + suffix)
                thumb_path = self.root / 'thumbnails' / (image_id + '.webp')
                record_data = {
                    'id': image_id,
                    'task_id': meta.get('task_id') or 'drive-sync',
                    'filename': name,
                    'local_path': str(local_path),
                    'local_cached': local_path.is_file(),
                    'width': meta.get('width'),
                    'height': meta.get('height'),
                    'media_type': meta.get('media_type') or ('video' if video else 'image'),
                    'mime_type': meta.get('mime_type') or f.get('mimeType') or mimetypes.guess_type(name)[0] or 'application/octet-stream',
                    'duration': meta.get('duration'),
                    'params': params,
                    'parent_id': meta.get('parent_id'),
                    'favorite': bool(meta.get('favorite', False)),
                    'tags': list(meta.get('tags') or []),
                    'project': proj_name,
                    'album': proj_name,
                    'drive_file_id': fid,
                    'metadata_file_id': sidecar.get('id') if sidecar else meta.get('metadata_file_id'),
                    'drive_parent_id': actual_parent,
                    'archive_status': 'saved',
                    'archive_error': None,
                    'url': '/api/gallery/' + image_id + '/download',
                    'thumbnail_url': '/api/gallery/' + image_id + '/thumbnail',
                    'preview_url': '/api/gallery/' + image_id + '/preview',
                }
                if meta.get('created_at') or f.get('modifiedTime'):
                    record_data['created_at'] = meta.get('created_at') or f.get('modifiedTime')
                if thumb_path.is_file() and not local_path.is_file():
                    record_data['local_cached'] = False
                saved = self.db.save('gallery', record_data)
                by_drive_id[fid] = saved
                added += 1

        return {
            'projects_found': len(project_folders),
            'added': added,
            'moved': moved,
            'skipped': skipped,
            'projects': self.list_projects()['projects'],
        }

    def detach_output(self, record):
        task_id, output_id = record.get('task_id'), record['id']
        task = self.db.get('tasks', task_id) if task_id else None
        if not task:
            return
        remaining = [r for r in task.get('results', []) if (r if isinstance(r, str) else r.get('id')) != output_id]
        live_ids = {r['id'] for r in self.db.list('gallery') if r.get('task_id') == task_id}
        remaining = [r for r in remaining if (r if isinstance(r, str) else r.get('id')) in live_ids]
        if task.get('status') == 'completed' and not live_ids:
            self.db.delete('tasks', task_id)
        else:
            self.db.save('tasks', {'id': task_id, 'results': remaining})
