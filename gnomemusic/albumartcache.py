# Copyright © 2018 The GNOME Music developers
#
# GNOME Music is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# GNOME Music is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with GNOME Music; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.
#
# The GNOME Music authors hereby grant permission for non-GPL compatible
# GStreamer plugins to be used and distributed together with GStreamer
# and GNOME Music.  This permission is above and beyond the permissions
# granted by the GPL license by which GNOME Music is covered.  If you
# modify this code, you may extend this exception to your version of the
# code, but you are not obligated to do so.  If you do not wish to do so,
# delete this exception statement from your version.

from enum import Enum
import logging
from math import pi
import os

import cairo
import gi
gi.require_version('GstTag', '1.0')
gi.require_version('MediaArt', '2.0')
from gi.repository import (Gdk, GdkPixbuf, Gio, GLib, GObject, Gtk, MediaArt,
                           Gst, GstTag, GstPbutils)

from gnomemusic import log


logger = logging.getLogger(__name__)


@log
def lookup_art_file_from_cache(coresong):
    """Lookup MediaArt cache art of an album or song.

    :param CoreSong coresong: song or album
    :returns: a cache file
    :rtype: Gio.File
    """
    try:
        album = coresong.props.album
    except AttributeError:
        album = coresong.props.title
    artist = coresong.props.artist

    success, thumb_file = MediaArt.get_file(artist, album, "album")
    if (not success
            or not thumb_file.query_exists()):
        return None

    return thumb_file


@log
def _make_icon_frame(icon_surface, art_size=None, scale=1, default_icon=False):
    border = 3
    degrees = pi / 180
    radius = 3

    icon_w = icon_surface.get_width()
    icon_h = icon_surface.get_height()
    ratio = icon_h / icon_w

    # Scale down the image according to the biggest axis
    if ratio > 1:
        w = int(art_size.width / ratio)
        h = art_size.height
    else:
        w = art_size.width
        h = int(art_size.height * ratio)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w * scale, h * scale)
    surface.set_device_scale(scale, scale)
    ctx = cairo.Context(surface)

    # draw outline
    ctx.new_sub_path()
    ctx.arc(w - radius, radius, radius - 0.5, -90 * degrees, 0 * degrees)
    ctx.arc(w - radius, h - radius, radius - 0.5, 0 * degrees, 90 * degrees)
    ctx.arc(radius, h - radius, radius - 0.5, 90 * degrees, 180 * degrees)
    ctx.arc(radius, radius, radius - 0.5, 180 * degrees, 270 * degrees)
    ctx.close_path()
    ctx.set_line_width(0.6)
    ctx.set_source_rgba(0, 0, 0, 0.7)
    ctx.stroke_preserve()

    # fill the center
    ctx.set_source_rgb(1, 1, 1)
    ctx.fill()

    matrix = cairo.Matrix()

    if default_icon:
        matrix.translate(-w * (1 / 3), -h * (1 / 3))
        ctx.set_operator(cairo.Operator.DIFFERENCE)
    else:
        matrix.scale(
            icon_w / ((w - border * 2) * scale),
            icon_h / ((h - border * 2) * scale))
        matrix.translate(-border, -border)

    ctx.set_source_surface(icon_surface, 0, 0)
    pattern = ctx.get_source()
    pattern.set_matrix(matrix)
    ctx.rectangle(border, border, w - border * 2, h - border * 2)
    ctx.fill()

    return surface


class DefaultIcon(GObject.GObject):
    """Provides the symbolic fallback and loading icons."""

    class Type(Enum):
        LOADING = 'content-loading-symbolic'
        MUSIC = 'folder-music-symbolic'

    _cache = {}
    _default_theme = Gtk.IconTheme.get_default()

    def __repr__(self):
        return '<DefaultIcon>'

    @log
    def __init__(self):
        super().__init__()

    @log
    def _make_default_icon(self, icon_type, art_size, scale):
        icon_info = self._default_theme.lookup_icon_for_scale(
            icon_type.value, art_size.width / 3, scale, 0)
        icon = icon_info.load_surface()

        icon_surface = _make_icon_frame(icon, art_size, scale, True)

        return icon_surface

    @log
    def get(self, icon_type, art_size, scale=1):
        """Returns the requested symbolic icon

        Returns a cairo surface of the requested symbolic icon in the
        given size.

        :param enum icon_type: The DefaultIcon.Type of the icon
        :param enum art_size: The Art.Size requested

        :return: The symbolic icon
        :rtype: cairo.Surface
        """
        if (icon_type, art_size, scale) not in self._cache.keys():
            new_icon = self._make_default_icon(icon_type, art_size, scale)
            self._cache[(icon_type, art_size, scale)] = new_icon

        return self._cache[(icon_type, art_size, scale)]


class Art(GObject.GObject):
    """Retrieves art for an album or song

    This is the control class for retrieving art.
    It looks for art in
    1. The MediaArt cache
    2. Embedded or in the directory
    3. Remotely
    """

    __gsignals__ = {
        'finished': (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    _blacklist = {}

    class Size(Enum):
        """Enum for icon sizes"""
        XSMALL = (34, 34)
        SMALL = (48, 48)
        MEDIUM = (128, 128)
        LARGE = (256, 256)
        XLARGE = (512, 512)

        def __init__(self, width, height):
            """Intialize width and height"""
            self.width = width
            self.height = height

    def __repr__(self):
        return '<Art>'

    @log
    def __init__(self, size, coresong, scale=1):
        super().__init__()

        self._size = size
        self._coresong = coresong
        # FIXME: Albums do not have a URL.
        try:
            self._url = self._coresong.props.url
        except AttributeError:
            self._url = None
        self._surface = None
        self._scale = scale

    @log
    def lookup(self):
        """Starts the art lookup sequence"""
        if self._in_blacklist():
            self._no_art_available()
            return

        cache = Cache()
        cache.connect('miss', self._cache_miss)
        cache.connect('hit', self._cache_hit)
        cache.query(self._coresong)

    @log
    def _cache_miss(self, klass):
        embedded_art = EmbeddedArt()
        embedded_art.connect('found', self._embedded_art_found)
        embedded_art.connect('unavailable', self._embedded_art_unavailable)
        embedded_art.query(self._coresong)

    @log
    def _cache_hit(self, klass, pixbuf):
        surface = Gdk.cairo_surface_create_from_pixbuf(
            pixbuf, self._scale, None)
        surface = _make_icon_frame(surface, self._size, self._scale)
        self._surface = surface

        self.emit('finished')

    @log
    def _embedded_art_found(self, klass):
        cache = Cache()
        # In case of an error in local art retrieval, there are two
        # options:
        # 1. Go and check for remote art instead
        # 2. Consider it a fail and add it to the blacklist
        # Go with option 1 here, because it gives the user the biggest
        # chance of getting artwork.
        cache.connect('miss', self._embedded_art_unavailable)
        cache.connect('hit', self._cache_hit)
        cache.query(self._coresong)

    @log
    def _embedded_art_unavailable(self, klass):
        remote_art = RemoteArt()
        remote_art.connect('retrieved', self._remote_art_retrieved)
        remote_art.connect('unavailable', self._remote_art_unavailable)
        remote_art.connect('no-remote-sources', self._remote_art_no_sources)
        remote_art.query(self._coresong)

    @log
    def _remote_art_retrieved(self, klass):
        cache = Cache()
        cache.connect('miss', self._remote_art_unavailable)
        cache.connect('hit', self._cache_hit)
        cache.query(self._coresong)

    @log
    def _remote_art_unavailable(self, klass):
        self._add_to_blacklist()
        self._no_art_available()

    @log
    def _remote_art_no_sources(self, klass):
        self._no_art_available()

    @log
    def _no_art_available(self):
        self._surface = DefaultIcon().get(
            DefaultIcon.Type.MUSIC, self._size, self._scale)

        self.emit('finished')

    @log
    def _add_to_blacklist(self):
        # FIXME: coresong can be a CoreAlbum
        try:
            album = self._coresong.props.album
        except AttributeError:
            album = self._coresong.props.title
        artist = self._coresong.props.artist

        if artist not in self._blacklist:
            self._blacklist[artist] = []

        album_stripped = MediaArt.strip_invalid_entities(album)
        self._blacklist[artist].append(album_stripped)

    @log
    def _in_blacklist(self):
        # FIXME: coresong can be a CoreAlbum
        try:
            album = self._coresong.props.album
        except AttributeError:
            album = self._coresong.props.title
        artist = self._coresong.props.artist
        album_stripped = MediaArt.strip_invalid_entities(album)

        if artist in self._blacklist:
            if album_stripped in self._blacklist[artist]:
                return True

        return False

    @GObject.Property
    def surface(self):
        if self._surface is None:
            self._surface = DefaultIcon().get(
                DefaultIcon.Type.LOADING, self._size, self._scale)

        return self._surface


class Cache(GObject.GObject):
    """Handles retrieval of MediaArt cache art

    Uses signals to indicate success or failure.
    """

    __gsignals__ = {
        'miss': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'hit': (GObject.SignalFlags.RUN_FIRST, None, (GObject.GObject, ))
    }

    def __repr__(self):
        return '<Cache>'

    @log
    def __init__(self):
        super().__init__()

        # FIXME: async
        self.cache_dir = os.path.join(GLib.get_user_cache_dir(), 'media-art')
        if not os.path.exists(self.cache_dir):
            try:
                Gio.file_new_for_path(self.cache_dir).make_directory(None)
            except GLib.Error as error:
                logger.warning(
                    "Error: {}, {}".format(error.domain, error.message))
                return

    @log
    def query(self, coresong):
        """Start the cache query

        :param CoreSong coresong: The CoreSong object to search art for
        """
        thumb_file = lookup_art_file_from_cache(coresong)
        if thumb_file:
            thumb_file.read_async(
                GLib.PRIORITY_LOW, None, self._open_stream, None)
            return

        self.emit('miss')

    @log
    def _open_stream(self, thumb_file, result, arguments):
        try:
            stream = thumb_file.read_finish(result)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))
            self.emit('miss')
            return

        GdkPixbuf.Pixbuf.new_from_stream_async(
            stream, None, self._pixbuf_loaded, None)

    @log
    def _pixbuf_loaded(self, stream, result, data):
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream_finish(result)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))
            self.emit('miss')
            return

        stream.close_async(GLib.PRIORITY_LOW, None, self._close_stream, None)
        self.emit('hit', pixbuf)

    @log
    def _close_stream(self, stream, result, data):
        try:
            stream.close_finish(result)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))


class EmbeddedArt(GObject.GObject):
    """Lookup local art

    1. Embedded through Gstreamer
    2. Available in the directory through MediaArt
    """

    __gsignals__ = {
        'found': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'unavailable': (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __repr__(self):
        return '<EmbeddedArt>'

    @log
    def __init__(self):
        super().__init__()

        try:
            Gst.init(None)
            GstPbutils.pb_utils_init()
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))
            return

        self._media_art = MediaArt.Process.new()

        self._album = None
        self._artist = None
        self._coresong = None
        self._path = None

    @log
    def query(self, coresong):
        """Start the local query

        :param CoreSong coresong: The CoreSong object to search art for
        """
        try:
            if coresong.props.url is None:
                self.emit('unavailable')
                return
        except AttributeError:
            self.emit('unavailable')
            return

        # FIXME: coresong can be a CoreAlbum
        try:
            self._album = coresong.props.album
        except AttributeError:
            self._album = coresong.props.title
        self._artist = coresong.props.artist
        self._coresong = coresong

        try:
            discoverer = GstPbutils.Discoverer.new(Gst.SECOND)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))
            self._lookup_cover_in_directory()
            return

        discoverer.connect('discovered', self._discovered)
        discoverer.start()

        success, path = MediaArt.get_path(self._artist, self._album, "album")

        if not success:
            self.emit('unavailable')
            discoverer.stop()
            return

        self._path = path

        success = discoverer.discover_uri_async(self._coresong.props.url)

        if not success:
            logger.warning("Could not add url to discoverer.")
            self.emit('unavailable')
            discoverer.stop()
            return

    @log
    def _discovered(self, discoverer, info, error):
        tags = info.get_tags()
        index = 0

        if (error is not None
                or tags is None):
            if error:
                logger.warning("Discoverer error: {}, {}".format(
                    Gst.CoreError(error.code), error.message))
            discoverer.stop()
            self.emit('unavailable')
            return

        while True:
            success, sample = tags.get_sample_index(Gst.TAG_IMAGE, index)
            if not success:
                break
            index += 1
            struct = sample.get_info()
            success, image_type = struct.get_enum(
                'image-type', GstTag.TagImageType)
            if not success:
                continue
            if image_type != GstTag.TagImageType.FRONT_COVER:
                continue

            buf = sample.get_buffer()
            success, map_info = buf.map(Gst.MapFlags.READ)
            if not success:
                continue

            try:
                mime = sample.get_caps().get_structure(0).get_name()
                MediaArt.buffer_to_jpeg(map_info.data, mime, self._path)
                self.emit('found')
                discoverer.stop()
                return
            except GLib.Error as error:
                logger.warning("Error: {}, {}".format(
                    MediaArt.Error(error.code), error.message))

        discoverer.stop()

        self._lookup_cover_in_directory()

    @log
    def _lookup_cover_in_directory(self):
        # Find local art in cover.jpeg files.
        self._media_art.uri_async(
            MediaArt.Type.ALBUM, MediaArt.ProcessFlags.NONE,
            self._coresong.props.url, self._artist, self._album,
            GLib.PRIORITY_LOW, None, self._uri_async_cb, None)

    @log
    def _uri_async_cb(self, src, result, data):
        try:
            success = self._media_art.uri_finish(result)
            if success:
                self.emit('found')
                return
        except GLib.Error as error:
            if MediaArt.Error(error.code) == MediaArt.Error.SYMLINK_FAILED:
                # This error indicates that the coverart has already
                # been linked by another concurrent lookup.
                self.emit('found')
                return
            else:
                logger.warning("Error: {}, {}".format(
                    MediaArt.Error(error.code), error.message))

        self.emit('unavailable')


class RemoteArt(GObject.GObject):
    """Looks for remote art through Grilo

    Uses Grilo coverart providers to retrieve art.
    """

    __gsignals__ = {
        'retrieved': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'unavailable': (GObject.SignalFlags.RUN_FIRST, None, ()),
        'no-remote-sources': (GObject.SignalFlags.RUN_FIRST, None, ())
    }

    def __repr__(self):
        return '<RemoteArt>'

    @log
    def __init__(self):
        super().__init__()

        self._artist = None
        self._album = None
        self._coresong = None
        self._grilo = None

    @log
    def query(self, coresong):
        """Start the remote query

        :param CoreSong coresong: The CoreSong object to search art for
        """
        # FIXME: coresong can be a CoreAlbum
        try:
            self._album = coresong.props.album
        except AttributeError:
            self._album = coresong.props.title
        self._artist = coresong.props.artist
        self._coresong = coresong

        self.emit('no-remote-sources')

        # FIXME: This is a total hack. It gets CoreModel from the
        # CoreAlbum or CoreSong about and then retrieves the CoreGrilo
        # instance.
        try:
            self._grilo = self._coresong._coremodel._grilo
        except AttributeError:
            self._grilo = self._coresong._grilo

        if not self._grilo.props.cover_sources:
            self.emit('no-remote-sources')
            self._grilo.connect(
                'notify::cover-sources', self._on_grilo_cover_sources_changed)
        else:
            # FIXME: It seems this Grilo query does not always return,
            # especially on queries with little info.
            self._grilo.get_album_art_for_item(
                self._coresong, self._remote_album_art)

    def _on_grilo_cover_sources_changed(self, klass, data):
        if self._grilo.props.cover_sources:
            self._grilo.get_album_art_for_item(
                self._coresong, self._remote_album_art)

    @log
    def _delete_callback(self, src, result, data):
        try:
            src.delete_finish(result)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))

    @log
    def _splice_callback(self, src, result, data):
        tmp_file, iostream = data

        iostream.close_async(
            GLib.PRIORITY_LOW, None, self._close_iostream_callback, None)

        try:
            src.splice_finish(result)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))
            self.emit('unavailable')
            return

        success, cache_path = MediaArt.get_path(
            self._artist, self._album, "album")

        if not success:
            self.emit('unavailable')
            return

        try:
            # FIXME: I/O blocking
            MediaArt.file_to_jpeg(tmp_file.get_path(), cache_path)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))
            self.emit('unavailable')
            return

        self.emit('retrieved')

        tmp_file.delete_async(
            GLib.PRIORITY_LOW, None, self._delete_callback, None)

    @log
    def _close_iostream_callback(self, src, result, data):
        try:
            src.close_finish(result)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))

    @log
    def _read_callback(self, src, result, data):
        try:
            istream = src.read_finish(result)
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))
            self.emit('unavailable')
            return

        try:
            [tmp_file, iostream] = Gio.File.new_tmp()
        except GLib.Error as error:
            logger.warning("Error: {}, {}".format(error.domain, error.message))
            self.emit('unavailable')
            return

        ostream = iostream.get_output_stream()
        # FIXME: Passing the iostream here, otherwise it gets
        # closed. PyGI specific issue?
        ostream.splice_async(
            istream, Gio.OutputStreamSpliceFlags.CLOSE_SOURCE
            | Gio.OutputStreamSpliceFlags.CLOSE_TARGET, GLib.PRIORITY_LOW,
            None, self._splice_callback, [tmp_file, iostream])

    @log
    def _remote_album_art(self, source, param, item, count, error):
        if error:
            logger.warning("Grilo error {}".format(error))
            self.emit('unavailable')
            return

        thumb_uri = item.get_thumbnail()

        if (thumb_uri is None
                or thumb_uri == ""):
            self.emit('unavailable')
            return

        src = Gio.File.new_for_uri(thumb_uri)
        src.read_async(
            GLib.PRIORITY_LOW, None, self._read_callback, None)
