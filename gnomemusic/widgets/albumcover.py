# Copyright 2018 The GNOME Music developers
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

import gi
gi.require_version('Grl', '0.3')
from gi.repository import Gdk, GLib, GObject, Gtk

from gnomemusic import log
from gnomemusic.albumartcache import Art
from gnomemusic.corealbum import CoreAlbum
from gnomemusic.widgets.twolinetip import TwoLineTip


@Gtk.Template(resource_path='/org/gnome/Music/ui/AlbumCover.ui')
class AlbumCover(Gtk.FlowBoxChild):
    """Cover tile as used in AlbumsView

    Includes cover, album title, artist & selection mode checkmark.
    """

    _nr_albums = 0

    __gtype_name__ = 'AlbumCover'

    _cover_stack = Gtk.Template.Child()
    _check = Gtk.Template.Child()
    _title_label = Gtk.Template.Child()
    _artist_label = Gtk.Template.Child()
    _events = Gtk.Template.Child()

    selected = GObject.Property(
        type=bool, default=False, flags=GObject.ParamFlags.READWRITE)
    selection_mode = GObject.Property(
        type=bool, default=False, flags=GObject.ParamFlags.READWRITE)

    def __repr__(self):
        return '<AlbumCover>'

    @log
    def __init__(self, corealbum):
        """Initialize the AlbumCover

        :param Grl.Media media: The media object to use
        """
        super().__init__()

        AlbumCover._nr_albums += 1

        self._corealbum = corealbum

        self._tooltip = TwoLineTip()

        artist = self._corealbum.props.artist
        title = self._corealbum.props.title

        self._tooltip.props.title = artist
        self._tooltip.props.subtitle = title

        self._artist_label.props.label = artist
        self._title_label.props.label = title

        self.bind_property(
            'selected', self._check, 'active',
            GObject.BindingFlags.BIDIRECTIONAL
            | GObject.BindingFlags.SYNC_CREATE)
        self.bind_property(
            'selection-mode', self._check, 'visible',
            GObject.BindingFlags.BIDIRECTIONAL)

        self.connect('query-tooltip', self._on_tooltip_query)

        self._events.add_events(Gdk.EventMask.TOUCH_MASK)

        self._cover_stack.props.size = Art.Size.MEDIUM

        self.show()

        # FIXME: To work around slow updating of the albumsview,
        # load album covers with a fixed delay. This results in a
        # quick first show with a placeholder cover and then a
        # reasonably responsive view while loading the actual
        # covers.
        GLib.timeout_add(
            50 * self._nr_albums, self._cover_stack.update, self._corealbum,
            priority=GLib.PRIORITY_LOW)

    @GObject.Property(type=CoreAlbum, flags=GObject.ParamFlags.READABLE)
    def corealbum(self):
        """CoreAlbum object used in AlbumCover

        :returns: The album used
        :rtype: CoreAlbum
        """
        return self._corealbum

    @Gtk.Template.Callback()
    @log
    def _on_album_event(self, evbox, event, data=None):
        modifiers = Gtk.accelerator_get_default_mod_mask()
        if ((event.get_state() & modifiers) == Gdk.ModifierType.CONTROL_MASK
                and not self.props.selection_mode):
            self.props.selection_mode = True

        if self.props.selection_mode:
            self.props.selected = not self.props.selected

    @Gtk.Template.Callback()
    @log
    def _on_tooltip_query(self, widget, x, y, kb, tooltip, data=None):
        tooltip.set_custom(self._tooltip)

        return True
