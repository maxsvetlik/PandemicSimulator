# Confidential, Copyright 2020, Sony Corporation of America, All rights reserved.
from typing import Optional, Sequence, List

import numpy as np

from .base import BasePerson
from ..interfaces import PersonState, LocationID, Risk, Registry, SimTime, NoOP, SimTimeTuple, NOOP, PersonRoutine, \
    ContactTracer, SimTimeInterval

__all__ = ['Minor']


class Minor(BasePerson):
    """Class that implements a school going minor."""

    _school: LocationID
    _school_time: SimTimeTuple
    _to_school_at_hour_prob: float

    _min_socializing_age: int

    _socializing_done: bool
    _to_social_at_hour_prob: float

    _outside_school_routines: Sequence[PersonRoutine]
    _outside_school_routines_due: List[bool]

    _to_home_hour_prob: float

    def __init__(self, age: int,
                 home: LocationID,
                 school: LocationID,
                 registry: Registry,
                 school_time: Optional[SimTimeTuple] = None,
                 outside_school_routines: Sequence[PersonRoutine] = (),
                 name: Optional[str] = None,
                 risk: Optional[Risk] = None,
                 night_hours: SimTimeTuple = SimTimeTuple(hours=tuple(range(0, 6)) + tuple(range(22, 24))),
                 regulation_compliance_prob: float = 1.0,
                 init_state: Optional[PersonState] = None,
                 numpy_rng: Optional[np.random.RandomState] = None):
        """
        :param age: Age of the person
        :param home: Home location id
        :param school: school location id
        :param registry: Registry instance to register the person and handle peron's entry to a location
        :param school_time: school time specified in SimTimeTuples. Default - 9am-5pm and Mon-Fri
        :param outside_school_routines: A sequence of person routines to run outside school time
        :param name: Optional name of the person
        :param risk: Optional health risk of the person
        :param night_hours: night hours - a person by default goes back home and stays at home
        :param regulation_compliance_prob: probability of complying to a regulation
        :param init_state: Optional initial state of the person
        :param numpy_rng: Random number generator
        """
        assert age <= 18, "A minor's age should be <= 18"
        self._school = school
        self._school_time = school_time or SimTimeTuple(hours=tuple(range(9, 15)), week_days=tuple(range(0, 5)))
        self._to_school_at_hour_prob = 0.95

        super().__init__(age=age,
                         home=home,
                         registry=registry,
                         name=name,
                         risk=risk,
                         night_hours=night_hours,
                         regulation_compliance_prob=regulation_compliance_prob,
                         init_state=init_state,
                         numpy_rng=numpy_rng)

        self._min_socializing_age = 12
        self._socializing_done = False

        self._to_social_at_hour_prob = 0.9

        self._outside_school_routines = outside_school_routines
        self._outside_school_routines_due = [False] * len(self._outside_school_routines)

        self._to_home_hour_prob = 0.5

    @property
    def school(self) -> LocationID:
        return self._school

    @property
    def assigned_locations(self) -> Sequence[LocationID]:
        return self._home, self._school

    @property
    def at_school(self) -> bool:
        """Return True if the person is at school and False otherwise"""
        return self._state.current_location == self.school

    def _sync(self, sim_time: SimTime) -> None:
        for i, routine in enumerate(self._outside_school_routines):
            if isinstance(routine.start_time, SimTimeTuple):
                self._outside_school_routines_due[i] = sim_time in routine.start_time
            elif isinstance(routine.start_time, SimTimeInterval):
                self._outside_school_routines_due[i] = (self._outside_school_routines_due[i] or
                                                        routine.start_time.trigger_at_interval(sim_time))
            else:
                raise ValueError(f'Unrecognized type of start_time specified. {type(routine.start_time)}')

        if sim_time.week_day == 0:
            self._socializing_done = False

    def step(self, sim_time: SimTime, contact_tracer: Optional[ContactTracer] = None) -> Optional[NoOP]:
        step_ret = super().step(sim_time, contact_tracer)
        if step_ret != NOOP:
            return step_ret

        # school time - go to school
        if not self.at_school and sim_time in self._school_time \
                and self._numpy_rng.uniform() < self._to_school_at_hour_prob:
            if self.enter_location(self.school):
                return None

        # outside school time
        if sim_time not in self._school_time:
            # execute due outside school routines
            for i, (routine, routine_due) in enumerate(zip(self._outside_school_routines,
                                                           self._outside_school_routines_due)):
                if (routine_due and
                        (routine.start_loc is None or routine.start_loc == self._state.current_location) and
                        self._numpy_rng.uniform() < routine.start_hour_probability):
                    end_loc = routine.end_loc
                    if (len(routine.end_locs) > 0) and (self._numpy_rng.uniform() < routine.explore_probability):
                        end_loc = routine.end_locs[self._numpy_rng.randint(0, len(routine.end_locs))]

                    if self.enter_location(end_loc):
                        self._outside_school_routines_due[i] = False
                        return None

            # if at home go to a social event if you have not been this week
            if (self.id.age >= self._min_socializing_age and
                    self.at_home and
                    not self._socializing_done and
                    self._numpy_rng.uniform() < self._to_social_at_hour_prob):
                social_loc = self._get_social_gathering_location()
                if social_loc is not None and self.enter_location(social_loc):
                    self._socializing_done = True
                    return None

            # if not at home go home
            if not self.at_home and self._numpy_rng.uniform() < self._to_home_hour_prob:
                self.enter_location(self.home)
                return None

        return NOOP
