cmake_minimum_required(VERSION 3.20)
project(AmpleComponentLayoutReaderDiagnostic LANGUAGES CXX)

add_executable(ample_component_layout_reader_test AmpleComponentLayoutReaderTest.cpp)
target_compile_features(ample_component_layout_reader_test PRIVATE cxx_std_17)
if(MSVC)
    target_compile_options(ample_component_layout_reader_test PRIVATE /W4 /WX /permissive-)
else()
    target_compile_options(ample_component_layout_reader_test PRIVATE -Wall -Wextra -Wpedantic -Werror)
endif()

enable_testing()
add_test(NAME ample_component_layout_reader_test
         COMMAND ample_component_layout_reader_test)
