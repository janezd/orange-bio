import sys
import os
import math
from collections import defaultdict
from datetime import date

from PyQt4.QtGui import (
    QAction, QHBoxLayout, QVBoxLayout, QWidget, QTreeWidget, QTreeWidgetItem,
    QComboBox, QGridLayout, QFrame, QToolButton, QSizePolicy, QStyledItemDelegate,
    QLineEdit, QCompleter, QListView,
    QStringListModel, QStandardItemModel, QStandardItem, QSortFilterProxyModel,
    QItemSelectionModel,
)
from PyQt4.QtCore import Qt, QSize, QTimer
from PyQt4.QtCore import pyqtSignal as Signal
import Orange.data
from Orange.widgets import widget, gui, settings
from Orange.widgets.utils.datacaching import data_hints

from ..utils import environ
from .. import dicty

try:
    from ast import literal_eval
except ImportError:
    # avoid eval on older pythons: dates are of lower importance than safety
    literal_eval = lambda x: None


def tfloat(s):
    try:
        return float(s)
    except:
        return None


class MyTreeWidgetItem(QTreeWidgetItem):

    def __init__(self, parent, *args):
        QTreeWidgetItem.__init__(self, parent, *args)
        self.par = parent

    def __contains__(self, text):
        return any(text.upper() in str(self.text(i)).upper() \
                   for i in range(self.columnCount()))

    def __lt__(self, o1):
        col = self.par.sortColumn()
        if col in [8, 9]:  # WARNING: hardcoded column numbers
            return tfloat(self.text(col)) < tfloat(o1.text(col))
        else:
            return QTreeWidgetItem.__lt__(self, o1)


# set buffer file
bufferpath = os.path.join(environ.buffer_dir, "pipax")

try:
    os.makedirs(bufferpath)
except OSError:
    pass

bufferfile = os.path.join(bufferpath, "database.sq3")


class SelectionByKey(object):

    """An object stores item selection by unique key values
    (works only for row selections in list and table models)
    Example::

        ## Save selection by unique tuple pairs (DisplayRole of column 1 and 2)
        selection = SelectionsByKey(itemView.selectionModel().selection(),
                                    key = (1,2))
        ## restore selection (Possibly omitting rows not present in the model)
        selection.select(itemView.selectionModel())

    """

    def __init__(self, itemSelection, name="", key=(0,)):
        self._key = key
        self.name = name
        self._selected_keys = []
        if itemSelection:
            self.setSelection(itemSelection)

    def _row_key(self, model, row):
        def key(row, col):
            return str(model.data(model.index(row, col), Qt.DisplayRole))

        return tuple(key(row, col) for col in self._key)

    def setSelection(self, itemSelection):
        self._selected_keys = [self._row_key(ind.model(), ind.row())
                               for ind in itemSelection.indexes()
                               if ind.column() == 0]

    def select(self, selectionModel):
        model = selectionModel.model()
        selectionModel.clear()
        for i in range(model.rowCount()):
            if self._row_key(model, i) in self._selected_keys:
                selectionModel.select(
                    model.index(i, 0),
                    QItemSelectionModel.Select | QItemSelectionModel.Rows)

    def __len__(self):
        return len(self._selected_keys)


class ListItemDelegate(QStyledItemDelegate):

    def sizeHint(self, option, index):
        size = QStyledItemDelegate.sizeHint(self, option, index)
        size = QSize(size.width(), size.height() + 4)
        return size

    def createEditor(self, parent, option, index):
        return QLineEdit(parent)

    def setEditorData(self, editor, index):
        editor.setText(str(index.data(Qt.DisplayRole)))

    def setModelData(self, editor, model, index):
        model.setData(index, editor.text(), Qt.EditRole)


class SelectionSetsWidget(QFrame):
    """
    Widget for managing multiple stored item selections
    """
    selectionModified = Signal(bool)

    def __init__(self, parent):
        QFrame.__init__(self, parent)
        self.setContentsMargins(0, 0, 0, 0)
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(1)
        self._setNameLineEdit = QLineEdit(self)
        layout.addWidget(self._setNameLineEdit)

        self._setListView = QListView(self)
        self._listModel = QStandardItemModel(self)
        self._proxyModel = QSortFilterProxyModel(self)
        self._proxyModel.setSourceModel(self._listModel)

        self._setListView.setModel(self._proxyModel)
        self._setListView.setItemDelegate(ListItemDelegate(self))

        self._setNameLineEdit.textChanged.connect(
            self._proxyModel.setFilterFixedString)

        self._completer = QCompleter(self._listModel, self)

        self._setNameLineEdit.setCompleter(self._completer)

        self._listModel.itemChanged.connect(self._onSetNameChange)
        layout.addWidget(self._setListView)
        buttonLayout = QHBoxLayout()

        self._addAction = QAction(
            "+", self, toolTip="Add a new sort key")
        self._updateAction = QAction(
            "Update", self, toolTip="Update/save current selection")
        self._removeAction = QAction(
            "\u2212", self, toolTip="Remove selected sort key.")

        self._addToolButton = QToolButton(self)
        self._updateToolButton = QToolButton(self)
        self._removeToolButton = QToolButton(self)
        self._updateToolButton.setSizePolicy(
                QSizePolicy.MinimumExpanding, QSizePolicy.Minimum)

        self._addToolButton.setDefaultAction(self._addAction)
        self._updateToolButton.setDefaultAction(self._updateAction)
        self._removeToolButton.setDefaultAction(self._removeAction)

        buttonLayout.addWidget(self._addToolButton)
        buttonLayout.addWidget(self._updateToolButton)
        buttonLayout.addWidget(self._removeToolButton)

        layout.addLayout(buttonLayout)
        self.setLayout(layout)

        self._addAction.triggered.connect(self.addCurrentSelection)
        self._updateAction.triggered.connect(self.updateSelectedSelection)
        self._removeAction.triggered.connect(self.removeSelectedSelection)

        self._setListView.selectionModel().selectionChanged.connect(
            self._onListViewSelectionChanged)
        self.selectionModel = None
        self._selections = []

    def sizeHint(self):
        size = QFrame.sizeHint(self)
        return QSize(size.width(), 200)

    def _onSelectionChanged(self, selected, deselected):
        self.setSelectionModified(True)

    def _onListViewSelectionChanged(self, selected, deselected):
        try:
            index = self._setListView.selectedIndexes()[0]
        except IndexError:
            return
        self.commitSelection(self._proxyModel.mapToSource(index).row())

    def _onSetNameChange(self, item):
        self.selections[item.row()].name = str(item.text())

    def _setButtonStates(self, val):
        self._updateToolButton.setEnabled(val)

    def setSelectionModel(self, selectionModel):
        if self.selectionModel:
            self.selectionModel.selectionChanged.disconnect(
                self._onSelectionChanged)
        self.selectionModel = selectionModel
        self.selectionModel.selectionChanged.connect(self._onSelectionChanged)

    def addCurrentSelection(self):
        item = self.addSelection(
            SelectionByKey(self.selectionModel.selection(),
                           name="New selection",
                           key=(1, 2, 3, 10)))
        index = self._proxyModel.mapFromSource(item.index())
        self._setListView.setCurrentIndex(index)
        self._setListView.edit(index)
        self.setSelectionModified(False)

    def removeSelectedSelection(self):
        i = self._proxyModel.mapToSource(self._setListView.currentIndex()).row()
        self._listModel.takeRow(i)
        del self.selections[i]

    def updateCurentSelection(self):
        i = self._proxyModel.mapToSource(self._setListView.selectedIndex()).row()
        self.selections[i].setSelection(self.selectionModel.selection())
        self.setSelectionModified(False)

    def addSelection(self, selection, name=""):
        self._selections.append(selection)
        item = QStandardItem(selection.name)
        item.setFlags(item.flags() ^ Qt.ItemIsDropEnabled)
        self._listModel.appendRow(item)
        self.setSelectionModified(False)
        return item

    def updateSelectedSelection(self):
        i = self._proxyModel.mapToSource(self._setListView.currentIndex()).row()
        self.selections[i].setSelection(self.selectionModel.selection())
        self.setSelectionModified(False)

    def setSelectionModified(self, val):
        self._selectionModified = val
        self._setButtonStates(val)
        self.selectionModified.emit(bool(val))

    def commitSelection(self, index):
        selection = self.selections[index]
        selection.select(self.selectionModel)

    def setSelections(self, selections):
        self._listModel.clear()
        for selection in selections:
            self.addSelection(selection)

    def selections(self):
        return self._selections

    selections = property(selections, setSelections)


class SortedListWidget(QWidget):
    sortingOrderChanged = Signal()

    class _MyItemDelegate(QStyledItemDelegate):

        def __init__(self, sortingModel, parent):
            QStyledItemDelegate.__init__(self, parent)
            self.sortingModel = sortingModel

        def sizeHint(self, option, index):
            size = QStyledItemDelegate.sizeHint(self, option, index)
            return QSize(size.width(), size.height() + 4)

        def createEditor(self, parent, option, index):
            cb = QComboBox(parent)
            cb.setModel(self.sortingModel)
            cb.showPopup()
            return cb

        def setEditorData(self, editor, index):
            pass  # TODO: sensible default

        def setModelData(self, editor, model, index):
            text = editor.currentText()
            model.setData(index, text)

    def __init__(self, *args):
        QWidget.__init__(self, *args)
        self.setContentsMargins(0, 0, 0, 0)
        gridLayout = QGridLayout()
        gridLayout.setContentsMargins(0, 0, 0, 0)
        gridLayout.setSpacing(1)

        model = QStandardItemModel(self)
        model.rowsInserted.connect(self.__changed)
        model.rowsRemoved.connect(self.__changed)
        model.dataChanged.connect(self.__changed)

        self._listView = QListView(self)
        self._listView.setModel(model)
#        self._listView.setDragEnabled(True)
        self._listView.setDropIndicatorShown(True)
        self._listView.setDragDropMode(QListView.InternalMove)
        self._listView.viewport().setAcceptDrops(True)
        self._listView.setMinimumHeight(100)

        gridLayout.addWidget(self._listView, 0, 0, 2, 2)

        vButtonLayout = QVBoxLayout()

        self._upAction = QAction(
            "\u2191", self, toolTip="Move up")

        self._upButton = QToolButton(self)
        self._upButton.setDefaultAction(self._upAction)
        self._upButton.setSizePolicy(
            QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)

        self._downAction = QAction(
            "\u2193", self, toolTip="Move down")

        self._downButton = QToolButton(self)
        self._downButton.setDefaultAction(self._downAction)
        self._downButton.setSizePolicy(
            QSizePolicy.Fixed, QSizePolicy.MinimumExpanding)

        vButtonLayout.addWidget(self._upButton)
        vButtonLayout.addWidget(self._downButton)

        gridLayout.addLayout(vButtonLayout, 0, 2, 2, 1)

        hButtonLayout = QHBoxLayout()

        self._addAction = QAction("+", self)
        self._addButton = QToolButton(self)
        self._addButton.setDefaultAction(self._addAction)

        self._removeAction = QAction("-", self)
        self._removeButton = QToolButton(self)
        self._removeButton.setDefaultAction(self._removeAction)
        hButtonLayout.addWidget(self._addButton)
        hButtonLayout.addWidget(self._removeButton)
        hButtonLayout.addStretch(10)
        gridLayout.addLayout(hButtonLayout, 2, 0, 1, 2)

        self.setLayout(gridLayout)
        self._addAction.triggered.connect(self._onAddAction)
        self._removeAction.triggered.connect(self._onRemoveAction)
        self._upAction.triggered.connect(self._onUpAction)
        self._downAction.triggered.connect(self._onDownAction)

    def sizeHint(self):
        size = QWidget.sizeHint(self)
        return QSize(size.width(), 100)

    def _onAddAction(self):
        item = QStandardItem("")
        item.setFlags(item.flags() ^ Qt.ItemIsDropEnabled)
        self._listView.model().appendRow(item)
        self._listView.setCurrentIndex(item.index())
        self._listView.edit(item.index())

    def _onRemoveAction(self):
        current = self._listView.currentIndex()
        self._listView.model().takeRow(current.row())

    def _onUpAction(self):
        row = self._listView.currentIndex().row()
        model = self._listView.model()
        if row > 0:
            items = model.takeRow(row)
            model.insertRow(row - 1, items)
            self._listView.setCurrentIndex(model.index(row - 1, 0))

    def _onDownAction(self):
        row = self._listView.currentIndex().row()
        model = self._listView.model()
        if row < model.rowCount() and row >= 0:
            items = model.takeRow(row)
            if row == model.rowCount():
                model.appendRow(items)
            else:
                model.insertRow(row + 1, items)
            self._listView.setCurrentIndex(model.index(row + 1, 0))

    def setModel(self, model):
        """ Set a model to select items from
        """
        self._model = model
        self._listView.setItemDelegate(self._MyItemDelegate(self._model, self))

    def addItem(self, *args):
        """ Add a new entry in the list
        """
        item = QStandardItem(*args)
        item.setFlags(item.flags() ^ Qt.ItemIsDropEnabled)
        self._listView.model().appendRow(item)

    def setItems(self, items):
        self._listView.model().clear()
        for item in items:
            self.addItem(item)

    def items(self):
        order = []
        for row in range(self._listView.model().rowCount()):
            order.append(str(self._listView.model().item(row, 0).text()))
        return order

    def __changed(self):
        self.sortingOrderChanged.emit()

    sortingOrder = property(items, setItems)


# Mapping from PIPAx.results_list annotation keys to Header names.
HEADER = [("_cached", ""),
          ("data_name", "Name"),
          ("species_name", "Species"),
          ("strain", "Strain"),
          ("Experiment", "Experiment"),
          ("genotype", "Genotype"),
          ("treatment", "Treatment"),
          ("growth", "Growth"),
          ("tp", "Timepoint"),
          ("replicate", "Replicate"),
          ("unique_id", "ID"),
          ("date_rnaseq", "Date RNAseq"),
          ("adapter_type", "Adapter"),
          ("experimenter", "Experimenter"),
          ("band", "Band"),
          ("polya", "Polya"),
          ("primer", "Primer"),
          ("shearing", "Shearing")
          ]

# Index of unique_id
ID_INDEX = 10

# Index of 'date_rnaseq'
DATE_INDEX = 11

SORTING_MODEL_LIST = \
    ["Strain", "Experiment", "Genotype",
     "Timepoint", "Growth", "Species",
     "ID", "Name", "Replicate"]


class OWPIPAx(widget.OWWidget):
    name = "PIPAx"
    description = "Access data from PIPA RNA-Seq database."
    icon = "../widgets/icons/PIPA.svg"
    priority = 35

    inputs = []
    outputs = [("Data", Orange.data.Table)]

    username = settings.Setting("")
    password = settings.Setting("")

    log2 = settings.Setting(False)
    rtypei = settings.Setting(5)  # hardcoded rpkm mapability polya
    excludeconstant = settings.Setting(False)
    joinreplicates = settings.Setting(False)
    #: The stored current selection (in experiments view)
    #: SelectionByKey | None
    currentSelection = settings.Setting(None)
    #: Stored selections (presets)
    #: list of SelectionByKey
    storedSelections = settings.Setting([])
    #: Stored column sort keys (from Sort view)
    #: list of strings
    storedSortingOrder = settings.Setting(
        ["Strain", "Experiment", "Genotype", "Timepoint"])

    experimentsHeaderState = settings.Setting(
        {name: False for _, name in HEADER[:ID_INDEX + 1]}
    )

    def __init__(self, parent=None, signalManager=None, name="PIPAx"):
        super().__init__(parent)

        self.selectedExperiments = []
        self.buffer = dicty.CacheSQLite(bufferfile)

        self.searchString = ""

        self.result_types = []
        self.mappings = {}

        self.controlArea.setMaximumWidth(250)
        self.controlArea.setMinimumWidth(250)

        gui.button(self.controlArea, self, "Reload",
                     callback=self.Reload)
        gui.button(self.controlArea, self, "Clear cache",
                     callback=self.clear_cache)

        b = gui.widgetBox(self.controlArea, "Experiment Sets")
        self.selectionSetsWidget = SelectionSetsWidget(self)
        self.selectionSetsWidget.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Maximum)

        def store_selections(modified):
            if not modified:
                self.storedSelections = self.selectionSetsWidget.selections

        self.selectionSetsWidget.selectionModified.connect(store_selections)
        b.layout().addWidget(self.selectionSetsWidget)

        gui.separator(self.controlArea)

        b = gui.widgetBox(self.controlArea, "Sort output columns")
        self.columnsSortingWidget = SortedListWidget(self)
        self.columnsSortingWidget.setSizePolicy(
            QSizePolicy.Preferred, QSizePolicy.Maximum)

        def store_sort_order():
            self.storedSortingOrder = self.columnsSortingWidget.sortingOrder
        self.columnsSortingWidget.sortingOrderChanged.connect(store_sort_order)
        b.layout().addWidget(self.columnsSortingWidget)
        sorting_model = QStringListModel(SORTING_MODEL_LIST)
        self.columnsSortingWidget.setModel(sorting_model)

        gui.separator(self.controlArea)

        box = gui.widgetBox(self.controlArea, 'Expression Type')
        self.expressionTypesCB = gui.comboBox(
            box, self, "rtypei", items=[], callback=self.UpdateResultsList)

        gui.checkBox(self.controlArea, self, "excludeconstant",
                     "Exclude labels with constant values")

        gui.checkBox(self.controlArea, self, "joinreplicates",
                     "Average replicates (use median)")

        gui.checkBox(self.controlArea, self, "log2",
                     "Logarithmic (base 2) transformation")

        self.commit_button = gui.button(self.controlArea, self, "&Commit",
                                        callback=self.Commit)
        self.commit_button.setDisabled(True)

        gui.rubber(self.controlArea)

        box = gui.widgetBox(self.controlArea, "Authentication")

        gui.lineEdit(box, self, "username", "Username:",
                     labelWidth=100,
                     orientation='horizontal',
                     callback=self.AuthChanged)

        self.passf = gui.lineEdit(box, self, "password", "Password:",
                                  labelWidth=100,
                                  orientation='horizontal',
                                  callback=self.AuthChanged)

        self.passf.setEchoMode(QLineEdit.Password)

        gui.lineEdit(self.mainArea, self, "searchString", "Search",
                     callbackOnType=True,
                     callback=self.SearchUpdate)

        self.headerLabels = [t[1] for t in HEADER]

        self.experimentsWidget = QTreeWidget()
        self.experimentsWidget.setHeaderLabels(self.headerLabels)
        self.experimentsWidget.setSelectionMode(QTreeWidget.ExtendedSelection)
        self.experimentsWidget.setRootIsDecorated(False)
        self.experimentsWidget.setSortingEnabled(True)

        contextEventFilter = gui.VisibleHeaderSectionContextEventFilter(
            self.experimentsWidget, self.experimentsWidget
        )

        self.experimentsWidget.header().installEventFilter(contextEventFilter)
        self.experimentsWidget.setItemDelegateForColumn(
            0, gui.IndicatorItemDelegate(self, role=Qt.DisplayRole))

        self.experimentsWidget.setAlternatingRowColors(True)

        self.experimentsWidget.selectionModel().selectionChanged.connect(
            self.onSelectionChanged)

        self.selectionSetsWidget.setSelectionModel(
            self.experimentsWidget.selectionModel()
        )

        self.mainArea.layout().addWidget(self.experimentsWidget)

        # Restore the selection states from the stored settings
        self.selectionSetsWidget.selections = self.storedSelections
        self.columnsSortingWidget.sortingOrder = self.storedSortingOrder

        self.restoreHeaderState()

        self.experimentsWidget.header().geometriesChanged.connect(
            self.saveHeaderState)

        self.dbc = None

        self.AuthSet()

        QTimer.singleShot(100, self.UpdateExperiments)

    def sizeHint(self):
        return QSize(800, 600)

    def AuthSet(self):
        if len(self.username):
            self.passf.setDisabled(False)
        else:
            self.passf.setDisabled(True)

    def AuthChanged(self):
        self.AuthSet()
        self.ConnectAndUpdate()

    def ConnectAndUpdate(self):
        self.Connect()
        self.UpdateExperiments(reload=True)

    def Connect(self):
        self.error(1)
        self.warning(1)

        def en(x):
            return x if len(x) else None

        self.dbc = dicty.PIPAx(cache=self.buffer,
                               username=en(self.username),
                               password=self.password)

        # check password
        if en(self.username) != None:
            try:
                self.dbc.mappings(reload=True)
            except dicty.AuthenticationError:
                self.error(1, "Wrong username or password")
                self.dbc = None
            except Exception as ex:
                print("Error when contacting the PIPA database", ex)
                sys.excepthook(*sys.exc_info())
                try:  # maybe cached?
                    self.dbc.mappings()
                    self.warning(1, "Can not access database - using cached data.")
                except Exception as ex:
                    self.dbc = None
                    self.error(1, "Can not access database.")

    def Reload(self):
        self.UpdateExperiments(reload=True)

    def clear_cache(self):
        self.buffer.clear()
        self.Reload()

    def rtype(self):
        """Return selected result template type """
        if self.result_types:
            return self.result_types[self.rtypei][0]
        else:
            return "-1"

    def UpdateExperimentTypes(self):
        self.expressionTypesCB.clear()
        items = [desc for _, desc in self.result_types]
        self.expressionTypesCB.addItems(items)
        self.rtypei = max(0, min(self.rtypei, len(self.result_types) - 1))

    def UpdateExperiments(self, reload=False):
        self.experimentsWidget.clear()
        self.items = []

        self.progressBarInit()

        if not self.dbc:
            self.Connect()

        mappings = {}
        result_types = []
        sucind = False  # success indicator for database index

        try:
            mappings = self.dbc.mappings(reload=reload)
            result_types = self.dbc.result_types(reload=reload)
            sucind = True
        except Exception as ex:
            try:
                mappings = self.dbc.mappings()
                result_types = self.dbc.result_types()
                self.warning(0, "Can not access database - using cached data.")
                sucind = True
            except Exception as ex:
                self.error(0, "Can not access database.")

        if sucind:
            self.warning(0)
            self.error(0)

        self.mappings = mappings
        self.result_types = result_types

        self.UpdateExperimentTypes()
        self.UpdateResultsList(reload=reload)

        self.progressBarFinished()

        if self.currentSelection:
            self.currentSelection.select(
                self.experimentsWidget.selectionModel())

        self.handle_commit_button()

    def UpdateResultsList(self, reload=False):

        results_list = {}
        try:
            results_list = self.dbc.results_list(self.rtype(), reload=reload)
        except Exception as ex:
            try:
                results_list = self.dbc.results_list(self.rtype())
            except Exception as ex:
                self.error(0, "Can not access database.")

        self.results_list = results_list
        mappings_key_dict = dict(((m["data_id"], m["id"]), key) \
                                 for key, m in self.mappings.items())

        def mapping_unique_id(annot):
            """Map annotations dict from results_list to unique
            `mappings` ids.
            """
            data_id, mappings_id = annot["data_id"], annot["mappings_id"]
            return mappings_key_dict[data_id, mappings_id]

        elements = []

        # softly change the view so that the selection stays the same

        items_shown = {}
        for i, item in enumerate(self.items):
            c = str(item.text(10))
            items_shown[c] = i

        items_to_show = dict((mapping_unique_id(annot), annot)
                             for annot in self.results_list.values())

        add_items = set(items_to_show) - set(items_shown)
        delete_items = set(items_shown) - set(items_to_show)

        i = 0
        while i < self.experimentsWidget.topLevelItemCount():
            it = self.experimentsWidget.topLevelItem(i)
            if str(it.text(10)) in delete_items:
                self.experimentsWidget.takeTopLevelItem(i)
            else:
                i += 1

        delete_ind = set([items_shown[i] for i in delete_items])
        self.items = [it for i, it in enumerate(self.items) if i not in delete_ind]

        for r_annot in [items_to_show[i] for i in add_items]:
            d = defaultdict(lambda: "?", r_annot)
            row_items = [""] + [d.get(key, "?") for key, _ in HEADER[1:]]
            try:
                time_dict = literal_eval(row_items[DATE_INDEX])
                date_rna = date(time_dict["fullYearUTC"],
                                time_dict["monthUTC"] + 1,  # Why is month 0 based?
                                time_dict["dateUTC"])
                row_items[DATE_INDEX] = date_rna.strftime("%x")
            except Exception:
                row_items[DATE_INDEX] = ''

            row_items[ID_INDEX] = mapping_unique_id(r_annot)
            elements.append(row_items)

            ci = MyTreeWidgetItem(self.experimentsWidget, row_items)

            self.items.append(ci)

        for i in range(len(self.headerLabels)):
            self.experimentsWidget.resizeColumnToContents(i)

        # which is the ok buffer version
        # FIXME: what attribute to use for version?
        self.wantbufver = \
            lambda x, ad=self.results_list: \
            defaultdict(lambda: "?", ad[x])["date"]

        self.wantbufver = lambda x: "0"

        self.UpdateCached()

    def UpdateCached(self):
        if self.wantbufver and self.dbc:
            fn = self.dbc.download_key_function()
            result_id_key = dict(((m["data_id"], m["mappings_id"]), key) \
                                 for key, m in self.results_list.items())

            for item in self.items:
                c = str(item.text(10))
                mapping = self.mappings[c]
                data_id, mappings_id = mapping["data_id"], mapping["id"]
                r_id = result_id_key[data_id, mappings_id]
                # Get the buffered version
                buffered = self.dbc.inBuffer(fn(r_id))
                value = " " if buffered == self.wantbufver(r_id) else ""
                item.setData(0, Qt.DisplayRole, value)

    def SearchUpdate(self, string=""):
        for item in self.items:
            item.setHidden(not all(s in item \
                                   for s in self.searchString.split())
                           )

    def Commit(self):
        if not self.dbc:
            self.Connect()

        pb = gui.ProgressBar(self, iterations=100)

        table = None

        ids = []
        for item in self.experimentsWidget.selectedItems():
            unique_id = str(item.text(10))
            annots = self.mappings[unique_id]
            ids.append((annots["data_id"], annots["id"]))

        transfn = None
        if self.log2:
            transfn = lambda x: math.log(x + 1.0, 2)

        reverse_header_dict = dict((name, key) for key, name in HEADER)

        hview = self.experimentsWidget.header()
        shownHeaders = [label for i, label in \
                        list(enumerate(self.headerLabels))[1:] \
                        if not hview.isSectionHidden(i)
                        ]

        allowed_labels = [reverse_header_dict.get(label, label) \
                          for label in shownHeaders]

        if self.joinreplicates and "id" not in allowed_labels:
            # need 'id' labels in join_replicates for attribute names
            allowed_labels.append("id")

        if len(ids):
            table = self.dbc.get_data(ids=ids, result_type=self.rtype(),
                                      callback=pb.advance,
                                      exclude_constant_labels=self.excludeconstant,
                                      #                          bufver=self.wantbufver,
                                      transform=transfn,
                                      allowed_labels=allowed_labels)

            if self.joinreplicates:
                table = dicty.join_replicates(table,
                                              ignorenames=["replicate", "data_id", "mappings_id",
                                                           "data_name", "id", "unique_id"],
                                              namefn=None,
                                              avg=dicty.median
                                              )

            # Sort attributes
            sortOrder = self.columnsSortingWidget.sortingOrder

            all_values = defaultdict(set)
            for at in table.domain.attributes:
                atts = at.attributes
                for name in sortOrder:
                    all_values[name].add(atts.get(reverse_header_dict[name], ""))

            isnum = {}
            for at, vals in all_values.items():
                vals = filter(None, vals)
                try:
                    for a in vals:
                        float(a)
                    isnum[at] = True
                except:
                    isnum[at] = False

            def optfloat(x, at):
                if x == "":
                    return ""
                else:
                    return float(x) if isnum[at] else x

            def sorting_key(attr):
                atts = attr.attributes
                return tuple([optfloat(atts.get(reverse_header_dict[name], ""), name) \
                              for name in sortOrder])

            attributes = sorted(table.domain.attributes,
                                key=sorting_key)

            domain = Orange.data.Domain(
                attributes, table.domain.class_var, table.domain.metas)
            table = table.from_table(domain, table)

            data_hints.set_hint(table, "taxid", "352472")
            data_hints.set_hint(table, "genesinrows", False)

            self.send("Data", table)

            self.UpdateCached()

        pb.finish()

    def onSelectionChanged(self, selected, deselected):
        self.handle_commit_button()

    def handle_commit_button(self):
        self.currentSelection = \
            SelectionByKey(self.experimentsWidget.selectionModel().selection(),
                           key=(1, 2, 3, 10))
        self.commit_button.setDisabled(not len(self.currentSelection))

    def saveHeaderState(self):
        hview = self.experimentsWidget.header()
        for i, label in enumerate(self.headerLabels):
            self.experimentsHeaderState[label] = hview.isSectionHidden(i)

    def restoreHeaderState(self):
        hview = self.experimentsWidget.header()
        state = self.experimentsHeaderState
        for i, label in enumerate(self.headerLabels):
            hview.setSectionHidden(i, state.get(label, True))
            self.experimentsWidget.resizeColumnToContents(i)


def test_main():
    from PyQt4.QtGui import QApplication
    app = QApplication(sys.argv)
    dicty.verbose = True
    w = OWPIPAx()
    w.show()
    r = app.exec_()
    w.saveSettings()
    return r

if __name__ == "__main__":
    sys.exit(test_main())
