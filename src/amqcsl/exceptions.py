class AMQCSLError(Exception): ...


class ClientDoesNotExistError(AMQCSLError): ...


class LoginError(AMQCSLError): ...


class QueryError(AMQCSLError): ...


class QueryTooLargeError(QueryError): ...


class ListCreateError(AMQCSLError): ...


class QuitError(AMQCSLError): ...
