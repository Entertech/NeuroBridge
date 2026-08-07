#ifndef AC_PRESSURE_HEADER_FILE_GUARD
#define AC_PRESSURE_HEADER_FILE_GUARD
#include "TypeDefine.h"
#include "SessionCache.h"
#include "NumCpp.hpp"
#include "SimpleHandler.h"
namespace ac
{
    struct PressureTemp
    {
        int index;
        nc::NdArray<double> wearFlagStore;
        basic::affection::handler::PressureHandlerTemp pressureTmp;
        vectord pressureRec;
    };

    struct PressureReportRes
    {
        vectord pressureRec;
        double pressureAvg;
    };

    class PressureComputing
    {
    public:
        double trigger(const basic::SessionCache &cache);
        PressureReportRes report();
        PressureComputing();
        ~PressureComputing();

    private:
        PressureTemp temp;
    };

}

#endif