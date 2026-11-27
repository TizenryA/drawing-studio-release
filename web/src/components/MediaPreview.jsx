import React, { useState, useEffect, useRef } from 'react';
import { enc } from '../api';
import { resolveMediaSources } from '../galleryPagination';

export const isVideo = item => item?.media_type === 'video' || /\.(mp4|webm)$/i.test(item?.filename || '');

export default function MediaPreview({
  item,
  alt = '',
  preferOriginal = false,
  className = '',
  style,
  onClick,
  ...props
}) {
  const video = isVideo(item);
  const { baseSrc, targetSrc, fallbackSrc } = resolveMediaSources(item, { preferOriginal });
  const [activeSrc, setActiveSrc] = useState(targetSrc);
  const [loaded, setLoaded] = useState(false);
  const [failed, setFailed] = useState(false);
  const [showBadge, setShowBadge] = useState(false);
  const hdRef = useRef(null);

  useEffect(() => {
    if (video) return;
    setActiveSrc(targetSrc);
    setFailed(false);
    const img = hdRef.current;
    if (img && img.getAttribute('src') === targetSrc && img.complete && img.naturalWidth > 0) {
      setLoaded(true);
    } else {
      setLoaded(false);
    }
  }, [video, item?.id, targetSrc]);

  useEffect(() => {
    if (video || loaded || failed) {
      setShowBadge(false);
      return;
    }
    const timer = setTimeout(() => setShowBadge(true), 120);
    return () => clearTimeout(timer);
  }, [video, loaded, failed, activeSrc]);

  if (video) {
    return (
      <video
        key={item.id}
        src={`/api/gallery/${enc(item.id)}/media`}
        poster={item.thumbnail_url}
        controls
        playsInline
        preload="metadata"
        aria-label={alt || '视频作品'}
        className={className}
        style={style}
        {...props}
      />
    );
  }

  const hasDims = Number(item?.width) > 0 && Number(item?.height) > 0;
  const mediaStyle = hasDims
    ? {
        ...style,
        '--media-w': Number(item.width),
        '--media-h': Number(item.height),
        aspectRatio: `${Number(item.width)} / ${Number(item.height)}`,
      }
    : style;

  return (
    <div
      className={`progressive-media ${loaded ? 'is-loaded' : ''} ${hasDims ? 'has-dims' : ''} ${className}`.trim()}
      onClick={onClick}
      style={mediaStyle}
    >
      {baseSrc && baseSrc !== activeSrc && (
        <img
          className="progressive-media-base"
          src={baseSrc}
          alt=""
          aria-hidden="true"
          decoding="async"
        />
      )}
      <img
        ref={hdRef}
        className={`progressive-media-hd ${loaded ? 'is-loaded' : ''}`}
        src={activeSrc}
        alt={alt}
        width={hasDims ? item.width : undefined}
        height={hasDims ? item.height : undefined}
        decoding="async"
        onLoad={() => {
          setLoaded(true);
          setShowBadge(false);
        }}
        onError={() => {
          if (activeSrc !== fallbackSrc && fallbackSrc) {
            setActiveSrc(fallbackSrc);
          } else {
            setFailed(true);
            setShowBadge(false);
          }
        }}
        {...props}
      />
      {!loaded && !failed && showBadge && (
        <span className="media-loading-pill">
          {preferOriginal ? '加载无损原图…' : '加载高清预览…'}
        </span>
      )}
    </div>
  );
}
