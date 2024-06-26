from DateTime import DateTime
from OFS.interfaces import IItem
from plone.app.contenttypes.behaviors.leadimage import ILeadImageBehavior
from plone.base.batch import Batch
from plone.base.interfaces import IPloneSiteRoot
from plone.base.interfaces.syndication import IFeed
from plone.base.interfaces.syndication import IFeedItem
from plone.base.interfaces.syndication import IFeedSettings
from plone.base.interfaces.syndication import ISearchFeed
from plone.base.navigationroot import get_navigation_root_object
from plone.dexterity.interfaces import IDexterityContent
from plone.namedfile.interfaces import INamedField
from plone.registry.interfaces import IRegistry
from plone.rfc822.interfaces import IPrimaryFieldInfo
from plone.uuid.interfaces import IUUID
from Products.CMFCore.utils import getToolByName
from Products.CMFPlone.utils import getSiteLogo
from Products.Five.browser.pagetemplatefile import ViewPageTemplateFile
from zope.cachedescriptors.property import Lazy as lazy_property
from zope.component import adapter
from zope.component import getMultiAdapter
from zope.component import getUtility
from zope.component import queryMultiAdapter
from zope.component.hooks import getSite
from zope.interface import implementer


class BaseFeedData:
    def __init__(self, context):
        self.context = context
        self.settings = IFeedSettings(context)
        self.site = getSite()
        if self.show_about:
            self.pm = getToolByName(self.context, "portal_membership")
        registry = getUtility(IRegistry)
        self.view_action_types = registry.get(
            "plone.types_use_view_action_in_listings", []
        )

    @lazy_property
    def show_about(self):
        return self.settings.show_author_info

    @property
    def link(self):
        return self.canonical_url

    @lazy_property
    def base_url(self):
        return self.context.absolute_url()

    @lazy_property
    def canonical_url(self):
        pcs = getMultiAdapter(
            (self.context, self.context.REQUEST), name="plone_context_state"
        )
        return pcs.canonical_object_url()

    @property
    def title(self):
        return self.context.Title()

    @property
    def description(self):
        return self.context.Description()

    @property
    def categories(self):
        return self.context.Subject()

    @property
    def published(self):
        date = self.context.EffectiveDate()
        if date and date != "None":
            return DateTime(date)

    @property
    def modified(self):
        date = self.context.ModificationDate()
        if date:
            return DateTime(date)

    @property
    def uid(self):
        uuid = IUUID(self.context, None)
        if uuid is None and hasattr(self.context, "UID"):
            return self.context.UID()
        return uuid

    @property
    def rights(self):
        return self.context.Rights()

    @property
    def publisher(self):
        if hasattr(self.context, "Publisher"):
            return self.context.Publisher()
        return "No Publisher"


@implementer(IFeed)
class FolderFeed(BaseFeedData):
    @lazy_property
    def author(self):
        if self.show_about:
            creator = self.context.Creator()
            member = self.pm.getMemberById(creator)
            return member

    @property
    def author_name(self):
        if self.author:
            return self.author.getProperty("fullname")

    @property
    def author_email(self):
        if self.author:
            return self.author.getProperty("email")

    @property
    def logo(self):
        return getSiteLogo(self.site)

    @property
    def icon(self):
        return "%s/favicon.ico" % self.site.absolute_url()

    def _brains(self):
        catalog = getToolByName(self.context, "portal_catalog")
        return catalog(
            path={"query": "/".join(self.context.getPhysicalPath()), "depth": 1}
        )

    def _items(self):
        """Do catalog query."""
        return [b.getObject() for b in self._brains()]

    @property
    def items(self):
        for item in self._items()[: self.limit]:
            # look for custom adapter
            # otherwise, just use default
            adapter = queryMultiAdapter((item, self), IFeedItem)
            if adapter is None:
                adapter = BaseItem(item, self)
            yield adapter

    @property
    def limit(self):
        return self.settings.max_items

    @property
    def language(self):
        langtool = getToolByName(self.context, "portal_languages")
        return langtool.getDefaultLanguage()


class CollectionFeed(FolderFeed):
    def _brains(self):
        # call the collection query method as defined in
        # plone.app.contenttypes.interfaces.ICollection
        # usually implemented at plone.aapp.contenttypes.item.collection
        return self.context.queryCatalog(batch=False)[: self.limit]


@implementer(ISearchFeed)
class SearchFeed(FolderFeed):
    def _brains(self):
        request = self.context.REQUEST
        navroot = get_navigation_root_object(self.context, IPloneSiteRoot(self.context))
        catalog = getToolByName(self.context, "portal_catalog")
        query = {
            "path": {"query": navroot.absolute_url_path(), "depth": 1},
            "sort_order": "reverse",
            "sort_on": request.get("sort_on", "effective"),
        }
        result = catalog(**query)
        start = int(request.get("b_start", 0))
        end = int(request.get("b_end", start + self.limit))
        return Batch(result, start, end)


@adapter(IItem, IFeed)
@implementer(IFeedItem)
class BaseItem(BaseFeedData):

    def __init__(self, context, feed):
        self.context = context
        self.feed = feed

    @lazy_property
    def creator(self):
        if hasattr(self.context, "Creator"):
            return self.context.Creator()

    @lazy_property
    def author(self):
        if self.feed.show_about:
            creator = self.context.Creator()
            member = self.feed.pm.getMemberById(creator)
            return member and member.getProperty("fullname") or creator

    @property
    def author_name(self):
        author = self.author
        if author and hasattr(author, "getProperty"):
            return author.getProperty("fullname")

    @property
    def author_email(self):
        author = self.author
        if author and hasattr(author, "getProperty"):
            return author.getProperty("email")

    @property
    def body(self):
        if hasattr(self.context, "getText"):
            value = self.context.getText()
        elif hasattr(self.context, "text"):
            value = self.context.text
        else:
            value = self.description
        if not isinstance(value, str):
            if hasattr(value, "output"):
                # could be RichTextValue object, needs transform
                value = value.output
        return value

    content_core_template = ViewPageTemplateFile("templates/content_core.pt")

    def render_content_core(self):
        self.request = self.context.REQUEST
        return self.content_core_template()

    @property
    def link(self):
        url = self.base_url
        if self.context.portal_type in self.feed.view_action_types:
            url = url + "/view"
        else:
            url = self.canonical_url
        return url

    guid = link

    @property
    def has_enclosure(self):
        return False

    @lazy_property
    def file(self):
        if self.has_enclosure:
            return self.context.getFile()

    @property
    def file_url(self):
        url = self.base_url
        fi = self.file
        if fi is not None:
            filename = fi.getFilename()
            if filename:
                url += "/@@download/file/%s" % filename
        return url

    @property
    def file_length(self):
        return self.file.get_size()

    @property
    def file_type(self):
        return self.file.getContentType()


@adapter(IDexterityContent, IFeed)
class DexterityItem(BaseItem):

    file = None
    field_name = ""

    def __init__(self, context, feed):
        super().__init__(context, feed)
        self.dexterity = IDexterityContent.providedBy(context)
        lead = ILeadImageBehavior(self.context, None)
        if lead:
            if (
                lead.image
                and hasattr(lead.image, "getSize")
                and lead.image.getSize() > 0
            ):
                self.file = lead.image
                self.field_name = "image"
        if self.file is None:
            try:
                primary = IPrimaryFieldInfo(self.context, None)
                if (
                    INamedField.providedBy(primary.field)
                    and hasattr(primary.value, "getSize")
                    and primary.value.getSize() > 0
                ):
                    self.file = primary.value
                    self.field_name = primary.fieldname
            except TypeError:
                pass

    @property
    def file_url(self):
        url = self.base_url
        fi = self.file
        if fi is not None:
            filename = fi.filename
            if filename:
                url += f"/@@download/{self.field_name}/{filename}"
        return url

    @property
    def has_enclosure(self):
        return self.file is not None

    @property
    def file_length(self):
        if self.has_enclosure:
            return self.file.getSize()
        return 0

    @property
    def file_type(self):
        if self.has_enclosure:
            return self.file.contentType
