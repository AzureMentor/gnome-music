# Copyright 2019 The GNOME Music developers
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

import logging

from gi.repository import GLib, GObject, Tracker

from gnomemusic import log
from gnomemusic.query import Query

logger = logging.getLogger(__name__)


class TrackerWrapper(GObject.GObject):
    """Create a connection to an instance of Tracker"""

    def __repr__(self):
        return "<TrackerWrapper>"

    def __init__(self):
        super().__init__()
        try:
            self._tracker = Tracker.SparqlConnection.get(None)
            self._tracker_available = True
        except Exception as e:
            self._tracker = None
            self._tracker_available = False
            logger.error(
                "Cannot connect to tracker, error {}\n".format(str(e)))

    @GObject.Property(type=object, flags=GObject.ParamFlags.READABLE)
    def tracker(self):
        return self._tracker

    @GObject.Property(
        type=bool, default=False, flags=GObject.ParamFlags.READABLE)
    def tracker_available(self):
        """Get Tracker availability.

        Tracker is available if a SparqlConnection has been opened and
        if a query can be performed.

        :returns: tracker availability
        :rtype: bool
        """
        return self._tracker_available

    @tracker_available.setter
    def tracker_available(self, value):
        """Set Tracker availability.

        If a SparqlConnection has not been opened, Tracker availability
        cannot be set to True.

        :param bool value: new value
        """
        if self._tracker is None:
            self._tracker_available = False
        else:
            self._tracker_available = value

    @log
    def local_songs_available(self, callback):
        """Checks if there are any local songs available.

        Calls a callback function with True or False depending on the
        availability of songs.
        :param callback: Function to call on result
        """
        def cursor_next_cb(conn, res, data):
            try:
                has_next = conn.next_finish(res)
            except GLib.Error as err:
                logger.warning("Error: {}, {}".format(err.__class__, err))
                callback(False)
                return

            if has_next:
                count = conn.get_integer(0)

                if count > 0:
                    callback(True)
                    return

            callback(False)

        def songs_query_cb(conn, res, data):
            try:
                cursor = conn.query_finish(res)
            except GLib.Error as err:
                logger.warning("Error: {}, {}".format(err.__class__, err))
                self.props.tracker_available = False
                callback(False)
                return

            cursor.next_async(None, cursor_next_cb, None)

        if not self.props.tracker_available:
            callback(False)
            return

        self._tracker.query_async(
            Query.all_songs_count(), None, songs_query_cb, None)
