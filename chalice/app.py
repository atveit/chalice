"""Chalice app and routing code."""
import re
import base64

# Implementation note:  This file is intended to be a standalone file
# that gets copied into the lambda deployment package.  It has no dependencies
# on other parts of chalice so it can stay small and lightweight, with minimal
# startup overhead.


_PARAMS = re.compile('{\w+}')


class ChaliceError(Exception):
    pass


class ChaliceViewError(ChaliceError):
    STATUS_CODE = 500

    def __init__(self, msg=''):
        super(ChaliceViewError, self).__init__(
            self.__class__.__name__ + ': %s' % msg)


class BadRequestError(ChaliceViewError):
    STATUS_CODE = 400


class NotFoundError(ChaliceViewError):
    STATUS_CODE = 404


ALL_ERRORS = [
    ChaliceViewError, BadRequestError, NotFoundError
]


class Request(object):
    """The current request from API gateway."""
    def __init__(self, query_params, headers, uri_params, method, body,
                 base64_body, context, stage_vars):
        self.query_params = query_params
        self.headers = headers
        self.uri_params = uri_params
        self.method = method
        #: The parsed JSON from the body.
        self.json_body = body
        # This is the raw base64 body.
        # We'll only bother decoding this if the user
        # actually requests this via the `.raw_body` property.
        self._base64_body = base64_body
        self._raw_body = None
        self.context = context
        self.stage_vars = stage_vars

    @property
    def raw_body(self):
        # Return the raw request body as bytes.
        if self._raw_body is None:
            self._raw_body = base64.b64decode(self._base64_body)
        return self._raw_body

    def to_dict(self):
        return self.__dict__.copy()


class RouteEntry(object):
    def __init__(self, view_function, view_name, path, methods):
        self.view_function = view_function
        self.view_name = view_name
        self.uri_pattern = path
        self.methods = methods
        #: A list of names to extract from path:
        #: e.g, '/foo/{bar}/{baz}/qux -> ['bar', 'baz']
        self.view_args = self._parse_view_args()

    def _parse_view_args(self):
        if '{' not in self.uri_pattern:
            return []
        # The [1:-1] slice is to remove the braces
        # e.g {foobar} -> foobar
        results = [r[1:-1] for r in _PARAMS.findall(self.uri_pattern)]
        return results

    def __eq__(self, other):
        return (
            self.view_function == other.view_function and
            self.view_name == other.view_name and
            self.uri_pattern == other.uri_pattern and
            self.view_args == other.view_args
        )


class Chalice(object):
    def __init__(self, app_name):
        self.app_name = app_name
        self.routes = {}
        self.current_request = None
        self.debug = False

    def route(self, path, **kwargs):
        def _register_view(view_func):
            self._add_route(path, view_func, **kwargs)
            return view_func
        return _register_view

    def _add_route(self, path, view_func, **kwargs):
        name = kwargs.get('name', view_func.__name__)
        methods = kwargs.get('methods', ['GET'])
        self.routes[path] = RouteEntry(view_func, name, path, methods)

    def __call__(self, event, context):
        # This is what's invoked via lambda.
        # Sometimes the event can be something that's not
        # what we specified in our request_template mapping.
        # When that happens, we want to give a better error message here.
        resource_path = event.get('context', {}).get('resource-path')
        if resource_path is None:
            raise ChaliceError(
                "Unknown request. (Did you forget to set the Content-Type "
                "header?)")
        http_method = event['context']['http-method']
        if resource_path not in self.routes:
            raise ChaliceError("No view function for: %s" % resource_path)
        route_entry = self.routes[resource_path]
        if http_method not in route_entry.methods:
            raise ChaliceError("Unsupported method: %s" % http_method)
        view_function = route_entry.view_function
        function_args = [event['params']['path'][name]
                         for name in route_entry.view_args]
        params = event['params']
        self.current_request = Request(params['querystring'],
                                       params['header'],
                                       params['path'],
                                       event['context']['http-method'],
                                       event['body-json'],
                                       event['base64-body'],
                                       event['context'],
                                       event['stage-variables'])
        try:
            response = view_function(*function_args)
        except Exception as e:
            if self.debug:
                # If the user has turned on debug mode,
                # we'll let the original exception propogate so
                # they get more information about what went wrong.
                raise e
            raise ChaliceViewError("An internal server error occurred.")
        return response
