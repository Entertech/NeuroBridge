#ifndef AC_RELAXATION_HEADER_FILE_GUARD
#define AC_RELAXATION_HEADER_FILE_GUARD
#include "TypeDefine.h"
#include "SessionCache.h"
#include "NumCpp.hpp"
#include "SimpleHandler.h"
namespace ac
{
    struct RelaxationTemp
    {
        int index;
        nc::NdArray<double> wearFlagStore;
        basic::affection::handler::RelaxationHandlerTemp relaxationTmp;
        vectord relaxationRec;
    };

    struct RelaxationReportRes
    {
        vectord relaxationRec;
        double relaxationAvg;
    };

    class RelaxationComputing
    {
    public:
        double trigger(const basic::SessionCache &cache);
        RelaxationReportRes report();
        RelaxationComputing();
        ~RelaxationComputing();

    private:
        RelaxationTemp temp;
    };

}

#endif