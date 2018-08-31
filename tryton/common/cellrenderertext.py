# This file is part of Tryton.  The COPYRIGHT file at the top level of
# this repository contains the full copyright notices and license terms.
import gtk
import gobject


class CellRendererText(gtk.CellRendererText):

    def do_start_editing(self, event, widget, path, background_area,
            cell_area, flags):
        return gtk.CellRendererText.do_start_editing(self, event, widget,
            path, background_area, cell_area, flags)


class CellRendererTextCompletion(CellRendererText):

    def __init__(self, set_completion):
        super(CellRendererTextCompletion, self).__init__()
        self.set_completion = set_completion

    def do_start_editing(self, event, widget, path, background_area, cell_area,
            flags):
        editable = super(CellRendererTextCompletion,
            self).do_start_editing(event, widget, path, background_area,
                cell_area, flags)
        self.set_completion(editable, path)
        return editable


gobject.type_register(CellRendererText)
gobject.type_register(CellRendererTextCompletion)
