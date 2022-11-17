import difflib
import hashlib
import itertools
import json
import os.path
import time
import webbrowser
from collections import defaultdict
from statistics import median
from typing import List, Tuple, Set, Dict

import dash
import networkx
import plotly.graph_objects as go
import networkx as nx
from dash import Dash, html, dcc, Input, Output, MATCH, ALL, State
import dash_bootstrap_components as dbc
import dash_bootstrap_templates

from pipeline_utils import find_pipeline


def generate_nx(job_tree: dict, job_data: dict) -> networkx.DiGraph:
    def get_nodes(d: dict, parent="", depth=0) -> Tuple[dict, List[Tuple[str, str]]]:
        _nodes = dict()
        _edges = []
        for name, data in d.items():
            if not name.startswith("__") and not name.endswith("__"):
                id = f"{parent}.{name}"
                if name in job_data:
                    status = data["__status__"]
                else:
                    status = data['__downstream_status__']
                if status is None:
                    status = "In Progress"
                _nodes[id] = {
                    "layer": depth,
                    "status": status,
                    "downstream_status": data['__downstream_status__'],
                    "url": job_data[name]["url"] if name in job_data else None,
                    "serial": job_data[name]["serial"] if name in job_data and "serial" in job_data[name] and
                                                          job_data[name]["serial"] else 0,
                    "name": name,
                }
                if parent:
                    _edges += [(parent, id)]
                new_nodes, new_edges = get_nodes(data, id, depth + 1)
                _nodes.update(new_nodes)
                _edges += new_edges
        return _nodes, _edges

    nodes, edges = get_nodes(job_tree)
    graph = nx.DiGraph()
    graph.add_edges_from(edges)
    for n, v in nodes.items():
        layer = v["layer"]
        del v["layer"]
        graph.add_node(n, layer=layer, data=v)
    return graph


def do_layout(g: networkx.DiGraph) -> int:
    def recurse(n, g, depth, y):
        first = True
        next_y = y
        for s in g.successors(n):
            if first:
                first = False
            else:
                next_y += 1
            next_y = recurse(s, g, depth + 1, next_y)
        next_ys = [g.nodes[s]["pos"][1] for s in g.successors(n)]
        median_y = median(next_ys) if next_ys else y
        g.nodes[n]["pos"] = (float(depth), median_y)
        return next_y

    first_nodes = [n for n in g.nodes() if g.nodes[n]["layer"] == 0]
    ny = 0
    for n in first_nodes:
        ny = recurse(n, g, 0, ny)
        ny += 2

    return ny


def generate_plot_figure(graph: networkx.Graph) -> go.Figure:
    start_time = time.process_time()
    # pos = nx.multipartite_layout(graph, subset_key="layer", center=(0,1))
    serial = str(max(float(graph.nodes[n]["data"]["serial"]) for n in graph.nodes()))
    y_scale = do_layout(graph)

    edge_traces = generate_edge_traces(graph)

    node_text_dict, node_trace = generate_node_traces(graph)
    annotations = get_node_labels(graph, node_text_dict)
    y_scale_limit = 100
    layoutButtons = list([
        dict(type="buttons",
             active=0 if y_scale < y_scale_limit else 1,
             buttons=list([
                 dict(label='Label:On',
                      method='update',
                      args=[{'visible': True}, {'annotations': annotations}]
                      ),
                 dict(label='Label:Off',
                      method='update',
                      args=[{'visible': True}, {'annotations': []}]
                      ),
             ]
             )
             )
    ]
    )
    fig = go.Figure(data=[*edge_traces.values(), node_trace],
                    layout=go.Layout(
                        titlefont_size=16,
                        showlegend=False,
                        hovermode='closest',
                        margin=dict(b=10, l=5, r=5, t=0, pad=0),
                        xaxis=dict(showgrid=False, zeroline=False, showticklabels=False),
                        yaxis=dict(
                            showgrid=False,
                            zeroline=False,
                            showticklabels=False,
                            range=[-4, y_scale],
                            constraintoward="top",
                        ),
                        height=y_scale * 15,
                        # paper_bgcolor="white",
                        # plot_bgcolor="white",
                        # title=f"Pipeline for {serial}",
                        # autosize=True,
                        annotations=annotations if y_scale < y_scale_limit else [],
                        updatemenus=layoutButtons,
                    ),
                    )
    fig.update
    # fig.update_layout(annotations=annotations)

    end_time = time.process_time()
    print(f"Rendered graph in {end_time - start_time} sec")
    return fig


def add_jobs_to_table(name: str,
                      job_struct: dict,
                      job_data: dict,
                      indent=1) -> html.Details:
    details = html.Details(
        children=[],
        id={
            "type": "details-job",
            "index": job_struct['__uuid__'],
        },
        className="details-job border",
        style={
            "text-indent": f"{indent*.5}em",
        }
    )
    status_classname_map = defaultdict(lambda: "alert-dark", {
        "FAILURE": "alert-danger",
        "SUCCESS": "alert-success",
        "UNSTABLE": "alert-warning",
        "In Progress": "alert-info",
        None: "alert-info",
        "default": "alert-dark",
    })
    if "__server__" in job_struct:
        fields = job_data[name]
        details.children.append(html.Summary(
            [
                html.Span(
                    fields["name"],
                    style={
                        "font-size": ".75rem",
                        "flex-grow": "1",
                    },
                ),
                html.Span(
                    fields["serial"],
                    style={"font-size": ".75rem", },
                ),
                html.Span(
                    [""],
                    style={
                        # "display": "inline-block",
                        # "width": "max-content",
                    },
                ),
                html.Span([
                    dbc.Button(
                        html.I(className="bi-diagram-2", style={"font-size": "1rem"}),
                        id={
                            "type": "btn-diagram",
                            "index": job_struct['__uuid__'],
                        },
                        outline=True,
                        color="secondary",
                        class_name="m-1",
                        style={"padding": "1px 2px 1px 2px", }
                    ),
                    dbc.Button(
                        html.I(className="bi-chevron-expand", style={"font-size": "1rem"}),
                        id={
                            "type": "btn-expand",
                            "index": job_struct['__uuid__'],
                        },
                        outline=True,
                        color="secondary",
                        class_name="m-1",
                        style={"padding": "1px 2px 1px 2px", }
                    ), ],
                    style={
                        "min-width": "68px",
                    },
                )
            ],
            className=f"{status_classname_map[job_struct.get('__downstream_status__', None)]} "
                      "d-flex justify-content-between align-items-center flex-wrap",
            style={
            }
        ))
        # table.add_row(
        #     prefix + fields["name"],
        #     fields["serial"],
        #     fields["build_num"],
        #     fields["timestamp"].strftime("%y-%m-%d %H:%M UTC") if fields["timestamp"] else None ,
        #     status(fields["status"]),
        #     fields["url"],
        #     )
        # if fields["timestamp"] and datetime.now() - fields["timestamp"] > timedelta(hours=24):
        #     table.rows[-1].style = "dim"
    else:
        details.children.append(html.Summary(
            [
                html.Span(
                    name,
                    style={
                        # "margin-left": "-0.3em",
                        "flex-grow": "1",
                    }
                ),
                html.Span([
                    dbc.Button(
                        html.I(className="bi-diagram-2", style={"font-size": "1rem"}),
                        id={
                            "type": "btn-diagram",
                            "index": job_struct['__uuid__'],
                        },
                        outline=True,
                        color="secondary",
                        class_name="m-1",
                        style={"padding": "1px 2px 1px 2px",}
                    ),
                    dbc.Button(
                        html.I(className="bi-chevron-expand", style={"font-size": "1rem"}),
                        id={
                            "type": "btn-expand",
                            "index": job_struct['__uuid__'],
                        },
                        outline=True,
                        color="secondary",
                        class_name="m-1",
                        style={"padding": "1px 2px 1px 2px", }
                    ), ],
                    style={
                        "min-width": "68px",
                    }
                ),

            ],
            className=f"{status_classname_map[job_struct.get('__downstream_status__', None)]} "
                      "d-flex justify-content-between flex-wrap",
            style={
                "display": "revert",
                # "width": "calc(100% - 1.1em)",
                # "margin-left": "3em",
            }
        ))

    d = html.Div(
        children=[],
    )
    for next_name in job_struct:
        if next_name.startswith("__") and next_name.endswith("__"):
            continue
        d.children.append(
            add_jobs_to_table(
                name=next_name,
                job_struct=job_struct[next_name],
                job_data=job_data,
                indent=indent + 1,
            ))
    details.children.append(d)

    return details


def get_node_labels(graph, node_text_dict):
    annotations = [go.layout.Annotation(
        x=graph.nodes[n]["pos"][0],
        y=graph.nodes[n]["pos"][1],
        xshift=5,
        yshift=5,
        xref="x",
        yref="y",
        text=node_text_dict.get(n, n),
        align="left",
        showarrow=False,
        yanchor="top",
        xanchor="left",
        textangle=25,
    ) for n in graph.nodes()]
    return annotations


def generate_node_traces(graph):
    node_trace = go.Scattergl(
        x=[pos[0] for _, pos in graph.nodes.data("pos")],
        y=[pos[1] for _, pos in graph.nodes.data("pos")],
        mode='markers',
        textposition="middle right",
        hovertemplate='%{customdata.name}<br>%{customdata.serial}<extra></extra>',
        showlegend=False,
        marker=dict(
            size=15,
            line_width=0.1,
        )
    )

    def find_unique_in_name(a: str, b: str):
        al = a.split("-")
        bl = b.split("-")
        for i, t in enumerate(al):
            if t != bl[i]:
                break
        return "-".join(bl[i:])

    node_text_dict = {
        edge[1]: find_unique_in_name(
            graph.nodes[edge[0]]["data"]["name"],
            graph.nodes[edge[1]]["data"]["name"]) for edge in graph.edges()
    }
    node_text = list(
        node_text_dict.get(n, n) for n in graph.nodes()
    )
    node_trace.text = node_text
    node_trace.customdata = [graph.nodes[n]["data"] for n in graph.nodes()]
    node_color = []
    for n in graph.nodes():
        status = graph.nodes[n]["data"]["status"]
        status_map = {
            "FAILURE": "darkred",
            "SUCCESS": "green",
            "UNSTABLE": "orange",
            "In Progress": "yellow",
            None: "yellow",
            "default": "lightgray",
        }
        node_color.append(status_map.get(status, status_map["default"]))
    node_trace.marker.color = node_color
    return node_text_dict, node_trace


def generate_edge_traces(graph):
    edge_colors = []
    for edge in graph.edges():
        status_map = {
            "FAILURE": "#ff6666",
            "SUCCESS": "green",
            "UNSTABLE": "orange",
            "In Progress": "#e6e600",
            None: "	#ff6666",
            "default": "gray",
        }
        status = graph.nodes[edge[1]]["data"]["downstream_status"]
        status_parent = graph.nodes[edge[0]]["data"]["downstream_status"]
        if status_parent == "NOT RUN":
            status = status_parent
        edge_colors.append(
            status_map.get(
                status,
                status_map["default"]
            ))
    edge_traces = {
        color: go.Scatter(
            x=list(
                itertools.chain.from_iterable((
                                                  graph.nodes[edge[0]]["pos"][0],
                                                  (graph.nodes[edge[0]]["pos"][0] + graph.nodes[edge[1]]["pos"][0]) / 2,
                                                  graph.nodes[edge[1]]["pos"][0],
                                                  None
                                              ) for i, edge in enumerate(graph.edges()) if edge_colors[i] == color)
            ),
            y=list(
                itertools.chain.from_iterable((
                                                  graph.nodes[edge[0]]["pos"][1],
                                                  graph.nodes[edge[1]]["pos"][1],
                                                  graph.nodes[edge[1]]["pos"][1],
                                                  None
                                              ) for i, edge in enumerate(graph.edges()) if edge_colors[i] == color)
            ),
            line=dict(width=3, color=color),
            hoverinfo='none',
            mode='lines',
        ) for color in set(edge_colors)
    }
    return edge_traces


def display_dash(pipeline_dict: dict, job_data: dict):
    start_time = time.process_time()
    graph = generate_nx(pipeline_dict, job_data)
    end_time = time.process_time()
    print(f"Generated network in {end_time - start_time} sec")
    app = Dash(
        __name__,
        external_stylesheets=[dbc.themes.BOOTSTRAP,
                              dbc.icons.BOOTSTRAP,
                              ]
    )
    dash_bootstrap_templates.load_figure_template()
    fig = generate_plot_figure(graph)
    graph = dcc.Graph(
        id='pipeline-graph',
        figure=fig,
        style={
            # "min-height": fig.layout.height,
            "height": "90vh",
            "display": "block",
        },
        responsive=True,
    )

    job_details = []
    for name, data in pipeline_dict.items():
        if name.startswith("__") and name.endswith("__"):
            continue
        job_details.append(
            add_jobs_to_table(
                name=name,
                job_struct=data,
                job_data=job_data,
            ))

    app.layout = dbc.Container(
        [
            dbc.Row([
                dbc.Col(
                    (
                        html.Div([
                            dbc.Button(
                                "Test",
                                id="btn-test"
                            ),
                            html.Div([], id="div-test"),
                            dbc.Button(
                                html.I(className="bi-diagram-2", style={"font-size": "1rem"}),
                                id={
                                    "type": "btn-diagram",
                                    "index": pipeline_dict['__uuid__'],
                                },
                                outline=True,
                                color="secondary",
                                class_name="m-1",
                                style={"padding": "1px 2px 1px 2px", }
                            ),
                            dbc.Button(
                                html.I(className="bi-chevron-expand", style={"font-size": "1rem"}),
                                id= {
                                    "type": "btn-expand",
                                    "index": pipeline_dict['__uuid__']
                                },
                                outline=True,
                                color="secondary",
                                class_name="m-1",
                                style={"padding": "1px 2px 1px 2px", }
                            ),
                        ]),
                        dbc.ListGroup(list(dbc.ListGroupItem(p) for p in job_details)),
                    ),
                    xxl=3, xl=4, lg=5, md=12, sm=12, xs=12),
                dbc.Col(
                    [dbc.Card([graph], body=True)],
                    xxl=9, xl=8, lg=7, xs=12
                ),
            ],
                class_name="g-2"
            ),
            html.Div(id="hidden-div", hidden=True),
        ],
        fluid=True,
        className="dbc",
    )

    # @app.callback(
    #     Output('hidden-div', 'children'),
    #     Input('pipeline-graph', 'clickData')
    # )
    # def display_click_data(clickData):
    #         url = clickData["points"][0]["customdata"]["url"]
    #         webbrowser.open(url)
    #         return html.A(url, href=url, target="_blank")
    #     except:
    #         return ""

    app.clientside_callback(
        """
        function(clickData) {
            url = clickData?.points[0]?.customdata?.url;
            if(url)
                window.open(url, "_blank");
            return null;
        }
        """,
        Output('hidden-div', 'children'),
        Input('pipeline-graph', 'clickData'),
        prevent_initial_call = True,
    )

    @app.callback(
        Output("pipeline-graph", "figure"),
        Input({"type": "btn-diagram", "index": ALL}, "n_clicks"),
        prevent_initial_call=True,
    )
    def click_diagram_btn(nclicks):
        start_time = time.process_time()
        start_id = dash.ctx.triggered_id["index"]
        sub_dict = find_pipeline(pipeline_dict, lambda _, p: p.get("__uuid__", "") == start_id)
        graph = generate_nx(sub_dict, job_data)
        end_time = time.process_time()
        print(f"Generated network in {end_time - start_time} sec")
        fig = generate_plot_figure(graph)
        return fig

    app.clientside_callback(
        """
        function(nclicks, id, open, children) {
            open = ! open;
            s = JSON.stringify(id, Object.keys(id).sort());
            dom = document.getElementById(s);
            elements = dom.getElementsByTagName("details");
            for (let e of elements)
                e.open = open;
            return open;
        }
        """,
        Output({"type": "details-job", "index": MATCH}, "open"),
        Input({"type": "btn-expand", "index": MATCH}, "n_clicks"),
        State({"type": "details-job", "index": MATCH}, "id"),
        State({"type": "details-job", "index": MATCH}, "open"),
        State({"type": "details-job", "index": MATCH}, "children"),
        prevent_initial_call=True,
    )
    app.run_server(debug=True)


