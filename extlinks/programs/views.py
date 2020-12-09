from datetime import date, datetime

from django.views.generic import ListView, DetailView
from django.views.decorators.cache import cache_page
from django.utils.decorators import method_decorator
from django.db.models import Sum, Count, Q

from extlinks.aggregates.models import (
    LinkAggregate,
    PageProjectAggregate,
    UserAggregate,
)
from extlinks.common.forms import FilterForm
from extlinks.organisations.models import Organisation
from .models import Program

from logging import getLogger

logger = getLogger("django")


class ProgramListView(ListView):
    model = Program

    def get_queryset(self, **kwargs):
        queryset = Program.objects.all().annotate(
            organisation_count=Count("organisation")
        )
        return queryset


@method_decorator(cache_page(60 * 60), name="dispatch")
class ProgramDetailView(DetailView):
    model = Program
    form_class = FilterForm

    def get_queryset(self, **kwargs):
        queryset = Program.objects.prefetch_related("organisation_set")
        return queryset

    def get_context_data(self, **kwargs):
        context = super(ProgramDetailView, self).get_context_data(**kwargs)
        this_program_organisations = self.object.organisation_set.all()
        context["organisations"] = this_program_organisations
        form = self.form_class(self.request.GET)
        context["form"] = form

        form_data = None
        # Filter queryset based on form, if used
        if form.is_valid():
            form_data = form.cleaned_data

        context = self._build_context_dictionary(
            this_program_organisations, context, form_data
        )

        context["query_string"] = self.request.META["QUERY_STRING"]

        return context

    def _build_context_dictionary(self, organisations, context, form_data):
        """
        This function builds the context dictionary that will populate the
        ProgramDetailView

        Parameters
        ----------
        organisations : List[Organisation]
            A list of organisations that belong to the program

        context : dict
            The context dictionary that the function will be adding information to

        form_data: dict|None
            If the filter form has valid filters, then there will be a dictionary
            to filter the aggregates tables by dates

        Returns
        -------
        dict : The context dictionary with the relevant statistics
        """
        if form_data:
            queryset_filter = self._build_queryset_filters(form_data, organisations)
        else:
            queryset_filter = Q(organisation__in=organisations)

        context = self._fill_chart_context(organisations, context, queryset_filter)
        context = self._fill_statistics_table_context(context, queryset_filter)
        context = self._fill_totals_tables(context, queryset_filter)

        return context

    def _fill_chart_context(self, organisations, context, queryset_filter):
        """
        This function adds the chart information to the context
        dictionary to display in ProgramDetailView

        Parameters
        ----------
        organisations : List[Organisation]
            A list of organisations that belong to the program

        context : dict
            The context dictionary that the function will be adding information to

        queryset_filter: Q
            If the information is filtered, this set of filters will filter it.
            The default is only filtering by the organisations that are part of
            the program

        Returns
        -------
        dict : The context dictionary with the relevant statistics
        """
        try:
            earliest_link_date = (
                LinkAggregate.objects.filter(queryset_filter)
                .earliest("full_date")
                .full_date
            )
        except LinkAggregate.DoesNotExist:
            earliest_link_date = (
                LinkAggregate.objects.filter(organisation__in=organisations)
                .earliest("full_date")
                .full_date
            )

        links_aggregated_date = (
            LinkAggregate.objects.filter(
                queryset_filter & Q(full_date__gte=earliest_link_date),
            )
            .values("month", "year")
            .annotate(
                net_change=Sum("total_links_added") - Sum("total_links_removed"),
            )
        )

        eventstream_dates = []
        eventstream_net_change = []
        for link in links_aggregated_date:
            date_combined = f"{link['year']}-{link['month']}"
            eventstream_dates.append(date_combined)
            eventstream_net_change.append(link["net_change"])

        # These stats are for filling the program net change chart
        context["eventstream_dates"] = eventstream_dates
        context["eventstream_net_change"] = eventstream_net_change

        return context

    def _fill_statistics_table_context(self, context, queryset_filter):
        """
        This function adds the Statistics table information to the context
        dictionary to display in ProgramDetailView

        Parameters
        ----------
        context : dict
            The context dictionary that the function will be adding information to

        queryset_filter: Q
            If the information is filtered, this set of filters will filter it.
            The default is only filtering by the organisations that are part of
            the program

        Returns
        -------
        dict : The context dictionary with the relevant statistics
        """
        links_added_removed = LinkAggregate.objects.filter(queryset_filter).aggregate(
            links_added=Sum("total_links_added"),
            links_removed=Sum("total_links_removed"),
            links_diff=Sum("total_links_added") - Sum("total_links_removed"),
        )
        context["total_added"] = links_added_removed["links_added"]
        context["total_removed"] = links_added_removed["links_removed"]
        context["total_diff"] = links_added_removed["links_diff"]

        editor_count = UserAggregate.objects.filter(queryset_filter).aggregate(
            editor_count=Count("username", distinct=True)
        )
        context["total_editors"] = editor_count["editor_count"]

        project_count = PageProjectAggregate.objects.filter(queryset_filter).aggregate(
            project_count=Count("project_name", distinct=True)
        )
        context["total_projects"] = project_count["project_count"]

        return context

    def _fill_totals_tables(self, context, queryset_filter):
        """
        This function adds the information for the Totals tables to the context
        dictionary to display in ProgramDetailView

        Parameters
        ----------
        context : dict
            The context dictionary that the function will be adding information to

        queryset_filter: Q
            If the information is filtered, this set of filters will filter it.
            The default is only filtering by the organisations that are part of
            the program

        Returns
        -------
        dict : The context dictionary with the relevant statistics
        """
        context["top_organisations"] = (
            LinkAggregate.objects.filter(queryset_filter)
            .values("organisation__pk", "organisation__name")
            .annotate(
                links_added=Sum("total_links_added"),
                links_removed=Sum("total_links_removed"),
                links_diff=Sum("total_links_added") - Sum("total_links_removed"),
            )
            .order_by("-links_diff", "-links_added", "-links_removed")
        )[:5]

        context["top_projects"] = (
            PageProjectAggregate.objects.filter(queryset_filter)
            .values("project_name")
            .annotate(
                links_added=Sum("total_links_added"),
                links_removed=Sum("total_links_removed"),
                links_diff=Sum("total_links_added") - Sum("total_links_removed"),
            )
            .order_by("-links_diff", "-links_added", "-links_removed")
        )[:5]

        context["top_users"] = (
            UserAggregate.objects.filter(queryset_filter)
            .values("username")
            .annotate(
                links_added=Sum("total_links_added"),
                links_removed=Sum("total_links_removed"),
                links_diff=Sum("total_links_added") - Sum("total_links_removed"),
            )
            .order_by("-links_diff", "-links_added", "-links_removed")
        )[:5]

        return context

    def _build_queryset_filters(self, form_data, organisations):
        """
        This function parses the form_data filter and creates Q object to filter
        the aggregates tables by

        Parameters
        ----------
        form_data: dict
            If the filter form has valid filters, then there will be a dictionary
            to filter the aggregates tables by dates

        organisations : List[Organisation]
            A list of organisations that belong to the program

        Returns
        -------
        Q : A Q object which will filter the aggregates queries
        """
        start_date_filter = None
        end_date_filter = None
        # The aggregates queries will always be filtered by organisation
        organisation_filter = Q(organisation__in=organisations)

        if "start_date" in form_data:
            start_date = form_data["start_date"]
            if start_date:
                start_date_filter = Q(full_date__gte=start_date)
        if "end_date" in form_data:
            end_date = form_data["end_date"]
            # The end date must not be greater than today's date
            if end_date:
                end_date_filter = Q(full_date__lte=end_date)

        if start_date_filter and end_date_filter:
            # If the start date is greater tham the end date, it won't filter
            # by date
            if start_date >= end_date:
                return organisation_filter
            return organisation_filter & start_date_filter & end_date_filter

        if start_date_filter and end_date_filter is None:
            return organisation_filter & start_date_filter

        if start_date_filter is None and end_date:
            return organisation_filter & end_date_filter

        return organisation_filter
